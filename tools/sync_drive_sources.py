"""Synchronise verified project flight sources from Google Drive.

This script is intended for GitHub Actions with a read-only Google Drive
service account. It downloads the three tracker workbooks, follows each
Checklist mission folder hyperlink, imports only coordinate-bearing flight
files, rebuilds coverage, and leaves git commit/push to the workflow.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coordinate_sources as cs  # noqa: E402
import export_magdata_trajectories as magarrow  # noqa: E402
import export_magarrow_planned_lines as magarrow_plans  # noqa: E402


FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPORTED_SUFFIXES = {".mrk", ".kmz", ".kml", ".csv", ".txt"}
ROOT_ARCHIVE_SUFFIXES = {".zip"}
SOURCE_PRIORITY = {".kmz": 0, ".mrk": 1, ".kml": 2, ".csv": 3, ".txt": 4}
FOLDER_ID_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
DATE_COMPACT_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
DATE_DASH_RE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{2})[-./](\d{2})(?!\d)")
MAGARROW_SOURCE_RE = re.compile(
    r"SRVY\d+-ACQU\d+(?:\s*\(\d+\))?(?:-10Hz)?\.(?:csv|magdata)$",
    re.IGNORECASE,
)
DJI_FILE_RE = re.compile(r"^DJI(?:[_\-\s]|$)", re.IGNORECASE)
DJI_MISSION_FOLDER_RE = re.compile(r"^DJI[_-]20\d{6}", re.IGNORECASE)
DJI_MISSION_TIME_RE = re.compile(r"DJI[_-]?(20\d{10})", re.IGNORECASE)
DJI_FLIGHT_RECORD_TIME_RE = re.compile(
    r"DJIFlightRecord[_-](20\d{2})-(\d{2})-(\d{2})[_-]\[(\d{2})-(\d{2})-(\d{2})\]",
    re.IGNORECASE,
)
RAW_DATA_FOLDER_KEYS = {"0rawdata", "rawdata"}
DJI_METADATA_FILE = "metadata.csv"
DJI_IMAGE_METADATA_FILE = "dji-image-metadata.csv"
DJI_FILE_COUNT_SENSORS = {4: "P1", 11: "L3"}
DRONE_FOLDER_RE = re.compile(
    r"(?:^|[^a-z0-9])(drone|uav|magnetic[ _-]*survey|magarrow|medusa|flight[ _-]*(?:log|record))(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
DRONE_SOURCE_RE = re.compile(
    r"(?:DJIFlightRecord|Timestamp|(?:^|[_-])DJI(?:[_-]|$)|SRVY\d*[-_]ACQU\d*|flight[ _-]*(?:log|record|track)|trajectory)",
    re.IGNORECASE,
)
MEDUSA_ARCHIVE_RE = re.compile(r"^MS-700.*\.zip$", re.IGNORECASE)
# Name patterns alone cannot decide whether a CSV/TXT holds coordinates, so
# unrecognised tables inside a configured survey branch are probed by header.
PROBE_SUFFIXES = {".csv", ".txt"}
PROBE_BYTES = 64 * 1024
MAX_HEADER_PROBES = 400
TRANSIENT_DRIVE_STATUSES = {429, 500, 502, 503, 504}
DRIVE_API_MAX_ATTEMPTS = 6


def clean(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def iso_date(value) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = clean(value)
    match = re.match(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}" if match else None


def source_date(name: str) -> str | None:
    match = DATE_DASH_RE.search(name) or DATE_COMPACT_RE.search(name)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def folder_id(value: str | None) -> str | None:
    match = FOLDER_ID_RE.search(value or "")
    return match.group(1) if match else None


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "source"


def comparable_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def tracker_rows(path: Path, project_key: str) -> list[dict]:
    # Imported here so the discovery and matching helpers stay importable (and
    # testable) on a runner that has no Drive/Excel dependencies installed.
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=False, read_only=False)
    sheet = workbook["Checklist"]
    current_date = None
    rows = []
    for row_number in range(3, sheet.max_row + 1):
        current_date = iso_date(sheet.cell(row_number, 3).value) or current_date
        mission = clean(sheet.cell(row_number, 5).value)
        if not mission:
            continue
        link_cell = sheet.cell(row_number, 26)
        link = link_cell.hyperlink.target if link_cell.hyperlink else clean(link_cell.value)
        linked_folder = folder_id(link)
        if not linked_folder:
            continue
        rows.append({
            "trackerId": f"{project_key}:{row_number}",
            "mission": mission,
            "date": current_date,
            "sensor": clean(sheet.cell(row_number, 4).value).upper(),
            "folderId": linked_folder,
        })
    return rows


def build_drive_service():
    raw = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON is not configured")
    try:
        service_account_info = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON is not valid JSON") from error
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def execute_with_retry(request, *, max_attempts: int = DRIVE_API_MAX_ATTEMPTS, sleep_fn=time.sleep):
    """Execute a Drive request, retrying quota and transient server failures."""
    for attempt in range(1, max_attempts + 1):
        try:
            return request.execute()
        except Exception as error:
            status = getattr(getattr(error, "resp", None), "status", None)
            if status not in TRANSIENT_DRIVE_STATUSES or attempt == max_attempts:
                raise
            delay = min(16, 2 ** (attempt - 1))
            print(
                "::warning::Drive API transient error "
                f"(status={status}, attempt={attempt}/{max_attempts}); retrying"
            )
            sleep_fn(delay)
    raise AssertionError("unreachable")


def download_next_chunk_with_retry(downloader):
    """Read one media chunk with the same transient retry budget as Drive requests."""
    return downloader.next_chunk(num_retries=max(0, DRIVE_API_MAX_ATTEMPTS - 1))


def metadata(service, file_id: str) -> dict:
    request = service.files().get(
        fileId=file_id,
        fields="id,name,mimeType,size,modifiedTime,md5Checksum,shortcutDetails,webViewLink",
        supportsAllDrives=True,
    )
    return execute_with_retry(request)


def list_children(service, parent_id: str) -> list[dict]:
    result = []
    token = None
    while True:
        request = service.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            fields=(
                "nextPageToken,files(id,name,mimeType,size,modifiedTime,md5Checksum,"
                "shortcutDetails,webViewLink,imageMediaMetadata(location,time))"
            ),
            pageSize=1000,
            pageToken=token,
            spaces="drive",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        response = execute_with_retry(request)
        result.extend(response.get("files", []))
        token = response.get("nextPageToken")
        if not token:
            return result


def list_named_children(service, parent_id: str, name: str) -> list[dict]:
    """List one exact filename without enumerating a mission's imagery."""
    escaped = name.replace("'", "\\'")
    request = service.files().list(
        q=f"'{parent_id}' in parents and name = '{escaped}' and trashed = false",
        fields="files(id,name,mimeType,size,modifiedTime,md5Checksum,shortcutDetails,webViewLink)",
        pageSize=100,
        spaces="drive",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    )
    return execute_with_retry(request).get("files", [])


def discover_magarrow_archive_sources(service, settings: dict) -> dict:
    """Find MagArrow plans and actual coordinate sources in one Drive archive."""
    root_folder_id = clean(settings.get("folderId"))
    if not root_folder_id:
        return {"plans": [], "tracks": []}
    max_depth = int(settings.get("scanDepth", 2))
    queue = [(root_folder_id, 0, "")]
    visited: set[str] = set()
    plans: list[dict] = []
    tracks: list[dict] = []
    while queue:
        current, depth, relative = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for raw_item in list_children(service, current):
            item = resolve_shortcut(service, raw_item)
            name = clean(item.get("name"))
            if item.get("mimeType") == FOLDER_MIME:
                if depth < max_depth:
                    queue.append((item["id"], depth + 1, str(Path(relative) / safe_filename(name))))
                continue
            suffix = Path(name).suffix.lower()
            candidate = {**item, "relativePath": str(Path(relative) / name)}
            if suffix == ".kmz":
                plans.append(candidate)
            elif suffix == ".magdata" and magarrow_acquisition_key(name):
                tracks.append(candidate)
            elif suffix in {".csv", ".txt"}:
                sample = probe_header_text(service, item)
                if sample and cs.header_has_coordinates(sample):
                    tracks.append(candidate)
    return {"plans": plans, "tracks": tracks}


def sync_magarrow_archive_plans(
    service, plan_sources: list[dict], repo: Path, workspace: Path
) -> dict:
    """Download and publish the configured MagArrow planned-line archive."""
    source_dir = workspace / "magarrow-planned-lines"
    source_dir.mkdir(parents=True, exist_ok=True)
    for item in plan_sources:
        destination = source_dir / Path(item.get("relativePath", safe_filename(item.get("name", "plan.kmz"))))
        destination = destination.with_name(safe_filename(destination.name))
        destination.parent.mkdir(parents=True, exist_ok=True)
        download_file(service, item, destination)
    downloaded = len(plan_sources)
    if not downloaded:
        raise RuntimeError("configured MagArrow archive contains no readable KMZ files")
    output = repo / "dist" / "data" / "magarrow" / "archive-planned-lines.geojson"
    payload = magarrow_plans.build(source_dir)
    if not payload.get("features"):
        raise RuntimeError("MagArrow KMZ files contained no valid planned LineString geometry")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dates = sorted({feature["properties"].get("date") for feature in payload["features"] if feature["properties"].get("date")})
    return {
        "downloadedFileCount": downloaded,
        "publishedLineCount": len(payload["features"]),
        "dateFrom": dates[0] if dates else None,
        "dateTo": dates[-1] if dates else None,
    }


def resolve_shortcut(service, item: dict) -> dict:
    if item.get("mimeType") != SHORTCUT_MIME:
        return item
    target_id = item.get("shortcutDetails", {}).get("targetId")
    return metadata(service, target_id) if target_id else item


def find_sources(service, root_folder_id: str, max_depth: int) -> list[dict]:
    # Enumerate a linked mission once. Most folders have no metadata.csv, so a
    # separate exact-name query followed by an image listing doubles the Drive
    # round trips. When metadata.csv is present it remains the preferred source;
    # otherwise the same listing supplies image EXIF GPS for the fallback CSV.
    found = []
    image_points = []
    queue = [(root_folder_id, 0)]
    visited = set()
    while queue:
        current, depth = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for raw_item in list_children(service, current):
            item = resolve_shortcut(service, raw_item)
            if item.get("mimeType") == FOLDER_MIME:
                if depth < max_depth:
                    queue.append((item["id"], depth + 1))
                continue
            image_metadata = item.get("imageMediaMetadata") or {}
            location = image_metadata.get("location") or {}
            if str(item.get("mimeType", "")).startswith("image/") and location:
                try:
                    latitude = float(location["latitude"])
                    longitude = float(location["longitude"])
                    altitude = float(location["altitude"]) if location.get("altitude") is not None else ""
                except (KeyError, TypeError, ValueError):
                    pass
                else:
                    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                        image_points.append({
                            "name": item.get("name", ""),
                            "latitude": latitude,
                            "longitude": longitude,
                            "altitude": altitude,
                            "time": image_metadata.get("time", ""),
                        })
            if Path(item.get("name", "")).suffix.lower() in SUPPORTED_SUFFIXES:
                found.append(item)
    if found or len(image_points) < 2:
        return found
    image_points.sort(key=lambda point: (point["time"], point["name"]))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["FileName", "GPSLatitude", "GPSLongitude", "GPSAltitude", "DateTime"])
    for point in image_points:
        writer.writerow([
            point["name"], point["latitude"], point["longitude"],
            point["altitude"], point["time"],
        ])
    synthetic_id = hashlib.sha256(root_folder_id.encode("utf-8")).hexdigest()[:24]
    return [{
        "id": f"dji-image-metadata-{synthetic_id}",
        "name": DJI_IMAGE_METADATA_FILE,
        "mimeType": "text/csv",
        "size": len(output.getvalue().encode("utf-8")),
        "syntheticCsv": output.getvalue(),
    }]


def magarrow_acquisition_key(value: str) -> str | None:
    text = clean(value)
    if not text:
        return None
    return magarrow.acquisition_name(text)


def magarrow_session_key(date_value: str | None, acquisition: str | None) -> tuple[str, str] | None:
    acquisition_key = magarrow_acquisition_key(acquisition or "")
    return (clean(date_value), acquisition_key) if acquisition_key else None


def magarrow_source_fingerprint(item: dict) -> str:
    identity = ":".join(str(item.get(key) or "") for key in ("id", "md5Checksum", "modifiedTime", "size"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def find_nergui_magarrow_sources(service, root_folder_id: str, max_depth: int) -> list[dict]:
    """Find verified MagArrow 10 Hz CSVs, with raw .magdata as a fallback."""
    found = []
    queue = [(root_folder_id, "", 0)]
    visited = set()
    while queue:
        current, relative, depth = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for raw_item in list_children(service, current):
            item = resolve_shortcut(service, raw_item)
            name = item.get("name", "")
            item_relative = f"{relative}/{name}".strip("/")
            if item.get("mimeType") == FOLDER_MIME:
                if depth < max_depth:
                    queue.append((item["id"], item_relative, depth + 1))
                continue
            if MAGARROW_SOURCE_RE.search(name):
                found.append({**item, "relativePath": item_relative})
    return found


def source_date_from_path(value: str) -> str | None:
    """Read a full date, or a YYYY ancestor plus MMDD child folder, from a Drive path."""
    direct = source_date(value)
    if direct:
        return direct
    parts = [part for part in re.split(r"[/\\]+", value) if part]
    for index, part in enumerate(parts):
        year_match = re.fullmatch(r"(?:\d+[._ -]*)?(20\d{2})", part)
        if not year_match:
            continue
        year = year_match.group(1)
        for child in parts[index + 1:]:
            day_match = re.match(r"(\d{2})(\d{2})(?:\D|$)", child)
            if not day_match:
                continue
            try:
                return date(int(year), int(day_match.group(1)), int(day_match.group(2))).isoformat()
            except ValueError:
                continue
    return None


def is_named_drone_source(name: str) -> bool:
    """Fast path: the file name itself identifies a known flight-source format."""
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return False
    if suffix == ".mrk":
        return True
    return bool(DRONE_SOURCE_RE.search(Path(name).name))


def probe_header_text(service, item: dict) -> str | None:
    """Download the first PROBE_BYTES of a file so its header can be read.

    Returns None when the bytes cannot be read or decoded. The file is only
    read; nothing in Drive is modified.
    """
    from googleapiclient.http import MediaIoBaseDownload

    try:
        request = service.files().get_media(fileId=item["id"], supportsAllDrives=True)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request, chunksize=PROBE_BYTES)
        download_next_chunk_with_retry(downloader)
        return buffer.getvalue().decode("utf-8-sig", errors="replace")
    except Exception:
        return None


def classify_candidate(service, item: dict, probe_budget: list[int]) -> tuple[bool, str]:
    """Decide whether a Drive item is a coordinate source. Returns (keep, how)."""
    name = item.get("name", "")
    suffix = Path(name).suffix.lower()
    if suffix in ROOT_ARCHIVE_SUFFIXES and MEDUSA_ARCHIVE_RE.search(Path(name).name):
        return True, "medusa-archive-name"
    if suffix not in SUPPORTED_SUFFIXES:
        return False, "unsupported-suffix"
    if is_named_drone_source(name):
        return True, "name-pattern"
    if suffix not in PROBE_SUFFIXES:
        return False, "name-pattern-miss"
    if probe_budget[0] <= 0:
        return False, "probe-budget-exhausted"
    probe_budget[0] -= 1
    text = probe_header_text(service, item)
    if text is None:
        return False, "probe-unreadable"
    header = cs.read_header(text)
    if header is None:
        return False, "probe-no-header"
    if cs.looks_like_point_table(header):
        # A GCP/sample coordinate table is not a flown trajectory.
        return False, "probe-point-table"
    if cs.detect_columns(header) is None:
        return False, "probe-no-coordinates"
    return True, "probe-header"


def find_drone_source_roots(service, root_folder_id: str, max_depth: int = 5) -> list[dict]:
    """Locate explicitly named survey branches under a project root.

    Generic Raw Data trees are intentionally excluded here: tracker-linked DJI
    missions are imported directly by ``find_sources``. Scanning the same raw
    tree again is redundant, expensive, and more likely to hit Drive quotas.
    """
    found = []
    queue = [(root_folder_id, "", 0)]
    visited = set()
    while queue:
        current, parent_path, depth = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for raw_item in list_children(service, current):
            item = resolve_shortcut(service, raw_item)
            if item.get("mimeType") != FOLDER_MIME:
                continue
            path = f"{parent_path}/{item.get('name', '')}".strip("/")
            if DRONE_FOLDER_RE.search(item.get("name", "")) or is_raw_data_folder(item.get("name", "")):
                found.append({**item, "relativePath": path})
                # The matching branch is scanned separately below, so do not
                # rediscover every nested raw/processed folder as another root.
                continue
            if depth < max_depth:
                queue.append((item["id"], path, depth + 1))
    return found


def find_drone_sources(service, root_folder_id: str, discovery_depth: int = 5, scan_depth: int = 8) -> tuple[list[dict], dict]:
    """Return strong coordinate-source candidates without exposing private paths in the summary."""
    roots = find_drone_source_roots(service, root_folder_id, discovery_depth)
    found = []
    seen_files = set()
    scanned_folders = 0
    inspected_files = 0
    probe_budget = [MAX_HEADER_PROBES]
    discovery_reasons: dict[str, int] = {}

    def consider(item: dict, path: str) -> None:
        nonlocal inspected_files
        if item.get("id") in seen_files:
            return
        inspected_files += 1
        if Path(item.get("name", "")).name.lower() == DJI_METADATA_FILE:
            # DJI mission exports place one coordinate-bearing metadata table
            # beside the images. The full parser still validates its header and
            # coordinates before anything is published.
            keep, how = True, "dji-metadata"
        else:
            keep, how = classify_candidate(service, item, probe_budget)
        discovery_reasons[how] = discovery_reasons.get(how, 0) + 1
        if not keep:
            return
        seen_files.add(item["id"])
        found.append({
            **item,
            "relativePath": path,
            "sourceDate": source_date_from_path(path),
            "discoveredBy": how,
        })

    for root in roots:
        queue = [(root["id"], root.get("relativePath", root.get("name", "")), 0)]
        visited = set()
        while queue:
            current, parent_path, depth = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            scanned_folders += 1
            for raw_item in list_children(service, current):
                item = resolve_shortcut(service, raw_item)
                path = f"{parent_path}/{item.get('name', '')}".strip("/")
                if item.get("mimeType") == FOLDER_MIME:
                    if DJI_MISSION_FOLDER_RE.search(item.get("name", "")):
                        # Mission directories often hold thousands of images.
                        # Tracker-linked discovery handles their direct files;
                        # root discovery only needs sibling Metadata folders.
                        scanned_folders += 1
                        continue
                    if depth < scan_depth:
                        queue.append((item["id"], path, depth + 1))
                    continue
                consider(item, path)
    dated = sorted(item["sourceDate"] for item in found if item.get("sourceDate"))
    suffix_counts = {}
    for item in found:
        suffix = Path(item.get("name", "")).suffix.lower().lstrip(".") or "unknown"
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
    summary = {
        "matchedBranchCount": len(roots),
        "scannedFolderCount": scanned_folders,
        "inspectedFileCount": inspected_files,
        "headerProbesUsed": MAX_HEADER_PROBES - probe_budget[0],
        "coordinateSourceCount": len(found),
        "sourceCountByType": dict(sorted(suffix_counts.items())),
        "discoveryDecisions": dict(sorted(discovery_reasons.items())),
        "dateFrom": dated[0] if dated else None,
        "dateTo": dated[-1] if dated else None,
    }
    return found, summary


def verbose_audit() -> bool:
    """Whether to print file-level audit rows.

    This repository is public, so its Actions logs are public too. File-level
    audit rows name Drive files that were inspected but never published, so
    they are off unless someone deliberately turns them on for a private run.
    """
    return os.environ.get("SYNC_VERBOSE_AUDIT", "").strip().lower() in {"1", "true", "yes"}


# Report keys that carry no file or folder name and are safe to print.
SAFE_REPORT_KEYS = (
    "project", "imported", "trackerRecords", "sourceFilesSeen", "reasonCounts",
    "declaredSources", "importedSources", "segments",
)


def redact_report(report: dict) -> dict:
    """Keep only the counting fields of an importer report."""
    redacted = {key: report[key] for key in SAFE_REPORT_KEYS if key in report}
    for key in ("unmatched", "rejected", "audit"):
        if report.get(key):
            redacted[f"{key}Count"] = len(report[key])
    return redacted


def match_campaign(campaigns: list[dict], project_key: str, candidate: dict) -> dict | None:
    """Return the one campaign that explicitly claims this source, else None.

    A campaign must declare the source; nothing is adopted because of where it
    happens to sit. Two competing campaigns resolve to no match.
    """
    name = candidate.get("name", "")
    path = candidate.get("relativePath", name)
    matched = []
    for campaign in campaigns:
        if campaign.get("projectKey") != project_key:
            continue
        rules = campaign.get("match", {})
        folders = rules.get("pathContains", [])
        if folders and not any(token.lower() in path.lower() for token in folders):
            continue
        patterns = rules.get("fileNamePatterns", [])
        if patterns and not any(re.search(pattern, name, re.IGNORECASE) for pattern in patterns):
            continue
        if not folders and not patterns:
            continue
        matched.append(campaign)
    return matched[0] if len(matched) == 1 else None


def folder_name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def is_raw_data_folder(value: str) -> bool:
    key = folder_name_key(value)
    return key in RAW_DATA_FOLDER_KEYS or re.sub(r"^\d+", "", key) == "rawdata"


def find_raw_data_folder(service, root_folder_id: str, max_depth: int = 2) -> dict | None:
    queue = [(root_folder_id, 0)]
    visited = set()
    while queue:
        current, depth = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for raw_item in list_children(service, current):
            item = resolve_shortcut(service, raw_item)
            if item.get("mimeType") != FOLDER_MIME:
                continue
            if is_raw_data_folder(item.get("name", "")):
                return item
            if depth < max_depth:
                queue.append((item["id"], depth + 1))
    return None


def raw_sensor_counts(service, root_folder_id: str, max_depth: int = 8) -> dict:
    """Return aggregate sensor counts without exposing private folder metadata."""
    raw_folder = find_raw_data_folder(service, root_folder_id)
    if not raw_folder:
        raise ValueError("Raw Data folder was not found under the configured root")
    sensors = {
        sensor: {"folderCount": 0, "djiFileCount": 0}
        for sensor in sorted(set(DJI_FILE_COUNT_SENSORS.values()))
    }
    unclassified_counts = {}
    scanned_folders = 0
    queue = [(raw_folder["id"], 0)]
    visited = set()
    while queue:
        current, depth = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        scanned_folders += 1
        children = [resolve_shortcut(service, item) for item in list_children(service, current)]
        child_folders = [item for item in children if item.get("mimeType") == FOLDER_MIME]
        dji_file_count = sum(
            1 for item in children
            if item.get("mimeType") != FOLDER_MIME and DJI_FILE_RE.search(item.get("name", ""))
        )
        if dji_file_count:
            sensor = DJI_FILE_COUNT_SENSORS.get(dji_file_count)
            if sensor:
                sensors[sensor]["folderCount"] += 1
                sensors[sensor]["djiFileCount"] += dji_file_count
            else:
                key = str(dji_file_count)
                unclassified_counts[key] = unclassified_counts.get(key, 0) + 1
        if depth < max_depth:
            queue.extend((item["id"], depth + 1) for item in child_folders)
    return {
        "classificationRule": {str(count): sensor for count, sensor in DJI_FILE_COUNT_SENSORS.items()},
        "scannedFolderCount": scanned_folders,
        "sensors": sensors,
        "unclassifiedFolderCountsByDjiFileCount": unclassified_counts,
    }


def choose_source(
    items: list[dict],
    tracker_date: str | None,
    mission: str,
    max_size: int,
    used_source_ids: set[str],
) -> dict | None:
    eligible = []
    for item in items:
        suffix = Path(item.get("name", "")).suffix.lower()
        item_date = source_date(item.get("name", ""))
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if item.get("id") in used_source_ids or suffix not in SUPPORTED_SUFFIXES or size > max_size:
            continue
        if tracker_date and item_date and item_date != tracker_date:
            continue
        eligible.append(item)
    if not eligible:
        return None

    # A linked folder can contain more than one mission from the same day. Prefer
    # the file whose name contains the complete tracker mission token, when that
    # is available, before applying the format preference.
    mission_key = comparable_name(mission)
    named_matches = [item for item in eligible if mission_key and mission_key in comparable_name(item.get("name", ""))]
    if named_matches:
        eligible = named_matches

    # Prefer a newly modified file within the best supported source type.
    eligible.sort(key=lambda item: item.get("modifiedTime", ""), reverse=True)
    eligible.sort(key=lambda item: SOURCE_PRIORITY.get(Path(item.get("name", "")).suffix.lower(), 99))
    return eligible[0]


def mission_timestamp(value: str) -> datetime | None:
    match = DJI_MISSION_TIME_RE.search(value or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M")
    except ValueError:
        return None


def flight_record_timestamp(value: str) -> datetime | None:
    match = DJI_FLIGHT_RECORD_TIME_RE.search(value or "")
    if not match:
        return None
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def source_sensor(value: str) -> str | None:
    match = re.search(r"(?:^|[^A-Za-z0-9])(L2|L3|P1)(?:$|[^A-Za-z0-9])", value or "", re.IGNORECASE)
    return match.group(1).upper() if match else None


def match_tracker_by_flight_time(candidate: dict, rows: list[dict], used_tracker_ids: set[str]) -> dict | None:
    """Match a central DJIFlightRecord log to the nearest unused tracker mission."""
    started = flight_record_timestamp(candidate.get("name", ""))
    if not started:
        return None
    sensor = source_sensor(candidate.get("relativePath", ""))
    eligible = []
    for row in rows:
        if row["trackerId"] in used_tracker_ids:
            continue
        if sensor and row.get("sensor") and sensor not in row["sensor"]:
            continue
        planned = mission_timestamp(row.get("mission", ""))
        if not planned or planned.date() != started.date():
            continue
        seconds = abs((planned - started).total_seconds())
        if seconds <= 20 * 60:
            eligible.append((seconds, row["trackerId"], row))
    eligible.sort(key=lambda item: (item[0], item[1]))
    return eligible[0][2] if eligible else None


def write_dji_frames_csv(frames, source_name: str, target: Path, stride: int = 10) -> int:
    """Write a compact 1-ish Hz coordinate table from decoded DJI OSD frames."""
    points = []
    for frame in frames:
        osd = getattr(frame, "osd", None)
        if osd is None:
            continue
        try:
            latitude = float(osd.latitude)
            longitude = float(osd.longitude)
        except (AttributeError, TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude == 0 and longitude == 0):
            continue
        try:
            altitude = float(osd.altitude)
        except (AttributeError, TypeError, ValueError):
            altitude = ""
        point = (latitude, longitude, altitude)
        if not points or point[:2] != points[-1][:2]:
            points.append(point)
    sampled = points[::max(1, stride)]
    if points and (not sampled or sampled[-1] != points[-1]):
        sampled.append(points[-1])
    if len(sampled) < 2:
        raise ValueError("decoded DJI log has fewer than two valid GPS positions")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["FileName", "GPSLatitude", "GPSLongitude", "RelativeAltitude"])
        writer.writerows((source_name, lat, lon, altitude) for lat, lon, altitude in sampled)
    return len(sampled)


def convert_dji_flight_record(source: Path, target: Path) -> int:
    """Decode a DJI v1-v14 FlightRecord locally; v13+ requires DJI_API_KEY."""
    from pydjirecord import DJILog

    log = DJILog.from_bytes(source.read_bytes())
    api_key = os.environ.get("DJI_API_KEY", "").strip()
    if log.version >= 13 and not api_key:
        raise RuntimeError("DJI_API_KEY is required for encrypted DJI FlightRecord v13+")
    keychains = log.fetch_keychains(api_key) if log.version >= 13 else None
    return write_dji_frames_csv(log.frames(keychains), source.name, target)


def download_file(service, item: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if "syntheticCsv" in item:
        target.write_text(item["syntheticCsv"], encoding="utf-8", newline="")
        return
    from googleapiclient.http import MediaIoBaseDownload

    if item.get("mimeType") == SHEET_MIME:
        request = service.files().export_media(fileId=item["id"], mimeType=XLSX_MIME)
    else:
        request = service.files().get_media(fileId=item["id"], supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request, chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = download_next_chunk_with_retry(downloader)
    target.write_bytes(buffer.getvalue())


def update_nergui_registry(registry_path: Path, acquisition_count: int, date_count: int) -> None:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in registry.get("datasets", [])}
    actual = by_id.get("magarrow-actual-tracks")
    coverage = by_id.get("magarrow-coverage-stats")
    raw = by_id.get("magarrow-raw")
    if actual:
        actual["sourceFile"] = f"Verified MagArrow navigation sources ({acquisition_count} acquisitions)"
        actual["notes"] = (
            f"Actual GNSS trajectories extracted from verified MagArrow navigation data in "
            f"{acquisition_count} acquisitions across {date_count} dates."
        )
    if coverage:
        coverage["sourceFile"] = f"Derived from {acquisition_count} verified MagArrow trajectories"
    if raw:
        raw["sourceFile"] = f"Verified MagArrow acquisition sources ({acquisition_count} acquisitions)"
        raw["notes"] = (
            f"Raw/converted MagArrow provenance for {acquisition_count} acquisitions. "
            "Only verified GNSS trajectory is exported to the browser."
        )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_nergui_magarrow(
    service,
    root_folder_id: str | None,
    repo: Path,
    workspace: Path,
    max_depth: int,
    max_size: int,
    additional_sources: list[dict] | None = None,
) -> dict:
    sources = find_nergui_magarrow_sources(service, root_folder_id, max_depth) if root_folder_id else []
    sources.extend(additional_sources or [])
    sources = list({item["id"]: item for item in sources}.values())
    by_session: dict[tuple[str, str], dict] = {}
    for item in sources:
        acquisition = magarrow_acquisition_key(item.get("name", ""))
        if not acquisition:
            continue
        source_day = source_date_from_path(item.get("relativePath", "")) or ""
        session = (source_day, acquisition)
        current = by_session.get(session)
        suffix = Path(item.get("name", "")).suffix.lower()
        candidate_priority = {".csv": 0, ".txt": 1, ".magdata": 2}.get(suffix, 9)
        current_priority = (
            {".csv": 0, ".txt": 1, ".magdata": 2}.get(
                Path(current.get("name", "")).suffix.lower(), 9
            ) if current else 9
        )
        if current is None or candidate_priority < current_priority or (
            candidate_priority == current_priority
            and item.get("modifiedTime", "") > current.get("modifiedTime", "")
        ):
            by_session[session] = item

    tracks_path = repo / "dist" / "data" / "magarrow" / "actual-tracks.geojson"
    coverage_path = repo / "dist" / "data" / "magarrow" / "coverage-stats.json"
    registry_path = repo / "dist" / "data" / "datasets.json"
    current_payload = json.loads(tracks_path.read_text(encoding="utf-8"))
    features_by_session = {
        magarrow_session_key(
            feature.get("properties", {}).get("date"),
            feature.get("properties", {}).get("acquisition", ""),
        ): feature
        for feature in current_payload.get("features", [])
        if magarrow_session_key(
            feature.get("properties", {}).get("date"),
            feature.get("properties", {}).get("acquisition", ""),
        )
    }
    source_dir = workspace / "nergui-magarrow-sources"
    parsed_dir = workspace / "nergui-magarrow-parsed"
    raw_parsed_dir = workspace / "nergui-magarrow-raw-staging"
    source_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    raw_parsed_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "discoveredSourceCount": len(sources),
        "chosenAcquisitionCount": len(by_session),
        "csvSourceCount": sum(1 for item in by_session.values() if item.get("name", "").lower().endswith(".csv")),
        "textSourceCount": sum(1 for item in by_session.values() if item.get("name", "").lower().endswith(".txt")),
        "magdataSourceCount": sum(1 for item in by_session.values() if item.get("name", "").lower().endswith(".magdata")),
        "retainedCount": 0,
        "downloadedCount": 0,
        "importedCount": 0,
        "updatedCount": 0,
        "skippedOversizeCount": 0,
        "failedCount": 0,
    }
    for session, item in sorted(by_session.items()):
        source_day, acquisition = session
        existing = features_by_session.get(session)
        properties = existing.get("properties", {}) if existing else {}
        fingerprint = magarrow_source_fingerprint(item)
        same_source = properties.get("sourceFingerprint") == fingerprint
        if existing and (same_source or not properties.get("sourceFingerprint")):
            summary["retainedCount"] += 1
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > max_size:
            summary["skippedOversizeCount"] += 1
            continue
        source_name = item.get("name", "")
        local_name = f"{item['id']}__{safe_filename(source_name)}"
        local_path = source_dir / local_name
        try:
            download_file(service, item, local_path)
            summary["downloadedCount"] += 1
            if source_name.lower().endswith(".csv"):
                try:
                    feature = magarrow.parse_10hz_csv(
                        local_path,
                        source_name=source_name,
                        source_fingerprint=fingerprint,
                    )
                except ValueError:
                    feature = magarrow.parse_coordinate_table(
                        local_path,
                        source_name=source_name,
                        fallback_date=source_day or None,
                        source_fingerprint=fingerprint,
                    )
            elif source_name.lower().endswith(".txt"):
                feature = magarrow.parse_coordinate_table(
                    local_path,
                    source_name=source_name,
                    fallback_date=source_day or None,
                    source_fingerprint=fingerprint,
                )
            else:
                source_date_value = source_day or source_date_from_path(item.get("relativePath", ""))
                if not source_date_value:
                    raise ValueError("raw MagArrow source has no verified date")
                dated_path = source_dir / f"{source_date_value}__{safe_filename(source_name)}"
                local_path.replace(dated_path)
                # parse_file writes its intermediate JSON. Keep that staging
                # output separate from parsed_dir, which is rebuilt below with
                # exactly one canonical feature per acquisition. Mixing the
                # two directories duplicates every raw .magdata acquisition.
                parsed_path = magarrow.parse_file(dated_path, raw_parsed_dir)
                feature = json.loads(parsed_path.read_text(encoding="utf-8"))
                feature["properties"].update({
                    "sourceFile": source_name,
                    "sourceFingerprint": fingerprint,
                })
            actual_session = magarrow_session_key(
                feature.get("properties", {}).get("date"), acquisition
            )
            if actual_session != session and source_day:
                raise ValueError("source path date does not match trajectory date")
            features_by_session[actual_session or session] = feature
            summary["updatedCount" if existing else "importedCount"] += 1
        except Exception as error:
            summary["failedCount"] += 1
            status = getattr(getattr(error, "resp", None), "status", None)
            print(
                "::warning::Nergui Undur MagArrow source import failed "
                f"(acquisition={acquisition}, error={type(error).__name__}, status={status})"
            )

    if summary["failedCount"] or summary["skippedOversizeCount"]:
        raise RuntimeError(
            "Nergui Undur MagArrow sync could not import every selected acquisition "
            f"(failed={summary['failedCount']}, oversize={summary['skippedOversizeCount']})"
        )

    for feature in features_by_session.values():
        feature.get("properties", {}).pop("sourceUrl", None)
        magarrow.write_feature(feature, parsed_dir)
    # The published base layers use plural filenames. Keep these paths aligned
    # with the repository rather than the singular names used by early fixtures.
    licence_path = repo / "dist" / "data" / "base" / "licenses.geojson"
    uchastik_path = repo / "dist" / "data" / "base" / "uchastics.geojson"
    magarrow.build(parsed_dir, licence_path, uchastik_path, tracks_path, coverage_path)
    dates = sorted({
        feature.get("properties", {}).get("date")
        for feature in features_by_session.values()
        if feature.get("properties", {}).get("date")
    })
    summary.update({
        "publishedAcquisitionCount": len(features_by_session),
        "publishedDateCount": len(dates),
        "dateFrom": dates[0] if dates else None,
        "dateTo": dates[-1] if dates else None,
    })
    update_nergui_registry(registry_path, len(features_by_session), len(dates))
    return summary


def run(command: list[str], cwd: Path) -> None:
    print("Running:", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def sync(config_path: Path, repo: Path, workspace: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    projects = config.get("projects", [])
    required_keys = {"hetsuu-hutul", "artsat", "buduunkhad", "nergui-undur"}
    if {item.get("key") for item in projects} != required_keys:
        raise ValueError("drive-sync-sources.json must configure exactly the four project trackers")
    if workspace.exists():
        shutil.rmtree(workspace)
    workbooks_dir = workspace / "workbooks"
    sources_dir = workspace / "sources"
    reports_dir = workspace / "reports"
    workbooks_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    service = build_drive_service()
    magarrow_archive_summary = None
    magarrow_archive_tracks: list[dict] = []
    if config.get("magArrowLineArchive"):
        try:
            archive_sources = discover_magarrow_archive_sources(
                service, config["magArrowLineArchive"]
            )
            magarrow_archive_tracks = archive_sources["tracks"]
            magarrow_archive_summary = sync_magarrow_archive_plans(
                service, archive_sources["plans"], repo, workspace
            )
            magarrow_archive_summary["actualTrackSourceCount"] = len(magarrow_archive_tracks)
            print("Nergui Undur MagArrow archive sync:")
            print(json.dumps(magarrow_archive_summary, ensure_ascii=False, indent=2))
        except Exception as error:
            print(f"::warning::Nergui Undur MagArrow archive sync skipped ({type(error).__name__})")
    raw_root_folder_id = os.environ.get("NERGUI_UNDUR_RAW_ROOT_FOLDER_ID", "").strip()
    raw_sensor_summary = None
    if raw_root_folder_id:
        try:
            raw_sensor_summary = raw_sensor_counts(service, raw_root_folder_id)
            print("Nergui Undur Raw Data sensor classification:")
            print(json.dumps(raw_sensor_summary, ensure_ascii=False, indent=2))
        except Exception as error:
            print(f"::warning::Nergui Undur Raw Data sensor classification skipped: {error}")
    else:
        print("::notice::NERGUI_UNDUR_RAW_ROOT_FOLDER_ID is not configured; Raw Data sensor classification skipped.")
    project_root_sources: dict[str, list[dict]] = {}
    project_root_summaries: dict[str, dict] = {}
    for project in projects:
        project_key = project["key"]
        env_name = project.get("rootEnvVar") or f"{project_key.upper().replace('-', '_')}_ROOT_FOLDER_ID"
        root_folder_id = os.environ.get(env_name, "").strip() or clean(project.get("rootFolderId"))
        if not root_folder_id:
            print(f"::notice::{project_key} has no project root configured; root discovery skipped.")
            continue
        try:
            sources, discovery = find_drone_sources(
                service,
                root_folder_id,
                int(config.get("projectRootDiscoveryDepth", 5)),
                int(config.get("projectRootScanDepth", 8)),
            )
            project_root_sources[project_key] = sources
            project_root_summaries[project_key] = discovery
            print(f"{project_key} project-root coordinate source discovery:")
            print(json.dumps(discovery, ensure_ascii=False, indent=2))
        except Exception as error:
            print(f"::warning::{project_key} project-root discovery skipped: {error}")
    max_depth = int(config.get("folderScanDepth", 2))
    max_size = int(config.get("maxSourceSizeBytes", 100 * 1024 * 1024))
    nergui_magarrow_summary = None
    if raw_root_folder_id or magarrow_archive_tracks:
        nergui_magarrow_summary = sync_nergui_magarrow(
            service,
            raw_root_folder_id or None,
            repo,
            workspace,
            int(config.get("nerguiMagArrowScanDepth", 8)),
            max_size,
            magarrow_archive_tracks,
        )
        print("Nergui Undur MagArrow sync:")
        print(json.dumps(nergui_magarrow_summary, ensure_ascii=False, indent=2))
    campaigns = config.get("campaigns", [])
    workbook_paths = []
    project_results = {}

    for project in projects:
        tracker_item = metadata(service, project["trackerFileId"])
        workbook_path = workbooks_dir / project["workbookName"]
        download_file(service, tracker_item, workbook_path)
        workbook_paths.append(workbook_path)
        rows = tracker_rows(workbook_path, project["key"])
        project_source_dir = sources_dir / project["key"]
        project_source_dir.mkdir(parents=True)
        mapping_entries = []
        mapped_tracker_ids: set[str] = set()
        seen_folders = {}
        used_source_ids: set[str] = set()
        mismatched = []
        stage = {
            "missionFoldersScanned": 0,
            "missionFoldersSkippedForRootIndex": 0,
            "missionFoldersUnreadable": 0,
            "candidateFilesFound": 0,
            "sourcesChosen": 0,
            "sourcesDownloaded": 0,
            "downloadFailures": 0,
            "flightRecordsAwaitingApiKey": 0,
        }
        reasons: dict[str, int] = {}

        def note(code: str) -> None:
            reasons[code] = reasons.get(code, 0) + 1

        for row in rows:
            if project["key"] in project_root_sources:
                stage["missionFoldersSkippedForRootIndex"] += 1
                continue
            candidates = seen_folders.get(row["folderId"])
            if candidates is None:
                try:
                    candidates = find_sources(service, row["folderId"], max_depth)
                    stage["missionFoldersScanned"] += 1
                except Exception as error:
                    # A folder the service account cannot read is an access
                    # problem, not a parsing bug.
                    candidates = []
                    stage["missionFoldersUnreadable"] += 1
                    print(f"::warning::{project['key']} mission folder unreadable ({type(error).__name__})")
                seen_folders[row["folderId"]] = candidates
                stage["candidateFilesFound"] += len(candidates)
            chosen = choose_source(candidates, row["date"], row["mission"], max_size, used_source_ids)
            if not chosen:
                wrong_dates = sorted({source_date(item.get("name", "")) for item in candidates if source_date(item.get("name", ""))})
                if wrong_dates:
                    mismatched.append({"trackerId": row["trackerId"], "date": row["date"], "sourceDates": wrong_dates})
                    note(cs.DATE_MISMATCH)
                elif not candidates:
                    note(cs.MISSING_SOURCE)
                else:
                    note(cs.MISSION_UNMATCHED)
                continue
            stage["sourcesChosen"] += 1
            used_source_ids.add(chosen["id"])
            local_name = f"{row['trackerId'].split(':')[-1].zfill(4)}__{safe_filename(chosen['name'])}"
            local_path = project_source_dir / local_name
            try:
                download_file(service, chosen, local_path)
            except Exception as error:
                stage["downloadFailures"] += 1
                note(cs.ACCESS_DENIED)
                print(f"::warning::{project['key']} source download failed ({type(error).__name__})")
                continue
            stage["sourcesDownloaded"] += 1
            mapping_entries.append({
                "file": local_name,
                "trackerId": row["trackerId"],
                "mission": row["mission"],
            })
            mapped_tracker_ids.add(row["trackerId"])
        campaign_entries: list[dict] = []
        root_sources = project_root_sources.get(project["key"], [])
        if root_sources:
            tracker_missions = {
                comparable_name(row["mission"]): row
                for row in rows
                if comparable_name(row.get("mission", ""))
            }
            campaign_source_dir = sources_dir / f"{project['key']}-campaign"
            campaign_source_dir.mkdir(parents=True, exist_ok=True)
            for candidate in sorted(root_sources, key=lambda item: item.get("relativePath", item.get("name", ""))):
                if candidate.get("id") in used_source_ids:
                    continue
                candidate_key = comparable_name(candidate.get("relativePath", candidate.get("name", "")))
                exact_rows = [row for key, row in tracker_missions.items() if key in candidate_key]
                if not exact_rows:
                    timed_row = match_tracker_by_flight_time(candidate, rows, mapped_tracker_ids)
                    exact_rows = [timed_row] if timed_row else []
                try:
                    size = int(candidate.get("size") or 0)
                except (TypeError, ValueError):
                    size = 0
                if size > max_size:
                    continue
                if len(exact_rows) > 1:
                    note(cs.AMBIGUOUS_MATCH)
                    continue
                campaign = match_campaign(campaigns, project["key"], candidate)
                if not exact_rows and not campaign:
                    # Not a 2026 tracker mission and not declared by any
                    # campaign, so it stays an inventory entry only. A source is
                    # never imported on the strength of its location alone.
                    note(cs.MISSION_UNMATCHED)
                    continue
                if (
                    exact_rows
                    and DJI_FLIGHT_RECORD_TIME_RE.search(candidate.get("name", ""))
                    and not os.environ.get("DJI_API_KEY", "").strip()
                ):
                    if stage["flightRecordsAwaitingApiKey"] == 0:
                        print(f"::notice::{project['key']} encrypted DJI FlightRecord logs await DJI_API_KEY")
                    stage["flightRecordsAwaitingApiKey"] += 1
                    mapped_tracker_ids.add(exact_rows[0]["trackerId"])
                    note(cs.UNSUPPORTED_FLIGHT_LOG)
                    continue
                used_source_ids.add(candidate["id"])
                if exact_rows:
                    local_name = (
                        f"root__{exact_rows[0]['trackerId'].split(':')[-1].zfill(4)}__"
                        f"{safe_filename(candidate['name'])}"
                    )
                    local_path = project_source_dir / local_name
                else:
                    source_day = candidate.get("sourceDate") or "undated"
                    local_name = f"{campaign['id']}__{source_day}__{safe_filename(candidate['name'])}"
                    local_path = campaign_source_dir / local_name
                try:
                    download_file(service, candidate, local_path)
                except Exception as error:
                    stage["downloadFailures"] += 1
                    note(cs.ACCESS_DENIED)
                    print(f"::warning::{project['key']} root source download failed ({type(error).__name__})")
                    continue
                stage["sourcesDownloaded"] += 1
                if exact_rows and DJI_FLIGHT_RECORD_TIME_RE.search(candidate.get("name", "")):
                    decoded_name = str(Path(local_name).with_suffix(".csv"))
                    decoded_path = project_source_dir / decoded_name
                    try:
                        convert_dji_flight_record(local_path, decoded_path)
                    except Exception as error:
                        note(cs.UNSUPPORTED_FLIGHT_LOG)
                        print(
                            f"::warning::{project['key']} DJI FlightRecord decode skipped "
                            f"({type(error).__name__})"
                        )
                        continue
                    local_name = decoded_name
                    local_path = decoded_path
                entry = {
                    "file": local_name,
                    "md5Checksum": candidate.get("md5Checksum"),
                    "sizeBytes": size,
                    "modifiedTime": candidate.get("modifiedTime"),
                    "discoveredBy": candidate.get("discoveredBy"),
                }
                if exact_rows:
                    mapped_tracker_ids.add(exact_rows[0]["trackerId"])
                    entry.update({
                        "trackerId": exact_rows[0]["trackerId"],
                        "mission": exact_rows[0]["mission"],
                    })
                    mapping_entries.append(entry)
                else:
                    entry["campaignId"] = campaign["id"]
                    entry["folderDate"] = candidate.get("sourceDate")
                    campaign_entries.append(entry)
        mapping_path = workspace / f"{project['key']}-mapping.json"
        mapping_path.write_text(json.dumps({"sources": mapping_entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        project_results[project["key"]] = {
            "trackerMissionRows": len(rows),
            "trackerRowsWithFolders": len(rows),
            "stages": stage,
            "reasons": dict(sorted(reasons.items())),
            "coordinateSources": len(mapping_entries),
            "campaignSources": len(campaign_entries),
            "dateMismatches": mismatched,
            "sourceDir": project_source_dir,
            "mapping": mapping_path,
        }
        if campaign_entries:
            campaign_mapping_path = workspace / f"{project['key']}-campaign-mapping.json"
            campaign_mapping_path.write_text(
                json.dumps({"sources": campaign_entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            project_results[project["key"]]["campaignSourceDir"] = sources_dir / f"{project['key']}-campaign"
            project_results[project["key"]]["campaignMapping"] = campaign_mapping_path
        if project["key"] in project_root_summaries:
            project_results[project["key"]]["projectRootDroneDiscovery"] = project_root_summaries[project["key"]]

    context_dir = repo / "dist" / "data" / "context"
    python = sys.executable
    run([
        python, str(repo / "tools" / "export_project_trackers.py"),
        *[str(path) for path in workbook_paths],
        "--licences", str(context_dir / "licenses.geojson"),
        "--summary-output", str(context_dir / "project-trackers.json"),
        "--points-output", str(context_dir / "project-control-points.geojson"),
    ], repo)

    tracks = context_dir / "project-flight-tracks.geojson"
    for project in projects:
        result = project_results[project["key"]]
        report = reports_dir / f"{project['key']}.json"
        run([
            python, str(repo / "tools" / "import_project_flight_tracks.py"),
            str(result["sourceDir"]),
            "--project", project["key"],
            "--trackers", str(context_dir / "project-trackers.json"),
            "--licences", str(context_dir / "licenses.geojson"),
            "--existing", str(tracks),
            "--output", str(tracks),
            "--mapping", str(result["mapping"]),
            "--report", str(report),
        ], repo)
        import_report = json.loads(report.read_text(encoding="utf-8"))
        result["importReport"] = redact_report(import_report)
        print(f"{project['key']} tracker import: {json.dumps(result['importReport'], ensure_ascii=False)}")
        if verbose_audit():
            print(json.dumps(import_report.get("audit", []), ensure_ascii=False, indent=2))
        result.pop("sourceDir")
        result.pop("mapping")

    campaign_tracks = context_dir / "project-campaign-tracks.geojson"
    for project in projects:
        result = project_results[project["key"]]
        campaign_dir = result.pop("campaignSourceDir", None)
        campaign_mapping = result.pop("campaignMapping", None)
        if not campaign_dir or not campaign_mapping:
            continue
        campaign_report = reports_dir / f"{project['key']}-campaign.json"
        run([
            python, str(repo / "tools" / "import_campaign_tracks.py"),
            str(campaign_dir),
            "--project", project["key"],
            "--campaigns", str(config_path),
            "--licences", str(context_dir / "licenses.geojson"),
            "--mapping", str(campaign_mapping),
            "--existing", str(campaign_tracks),
            "--output", str(campaign_tracks),
            "--audit", str(campaign_report),
        ], repo)
        payload = json.loads(campaign_report.read_text(encoding="utf-8"))
        result["campaignImportReport"] = redact_report(payload)
        print(f"{project['key']} campaign import: {json.dumps(result['campaignImportReport'], ensure_ascii=False)}")
        if verbose_audit():
            print(json.dumps(payload.get("audit", []), ensure_ascii=False, indent=2))

    run([
        python, str(repo / "tools" / "export_project_flight_coverage.py"),
        str(tracks),
        "--licences", str(context_dir / "licenses.geojson"),
        "--output", str(context_dir / "project-flight-coverage.json"),
    ], repo)
    summary = {
        "projects": project_results,
        "nerguiUndurRawSensorCounts": raw_sensor_summary,
        "nerguiUndurMagArrow": nergui_magarrow_summary,
        "nerguiUndurMagArrowArchive": magarrow_archive_summary,
        "projectRootDroneSources": project_root_summaries,
        "secretsConfigured": {
            "ARTSAT_ROOT_FOLDER_ID": bool(os.environ.get("ARTSAT_ROOT_FOLDER_ID", "").strip()),
            "HETSUU_HUTUL_ROOT_FOLDER_ID": bool(os.environ.get("HETSUU_HUTUL_ROOT_FOLDER_ID", "").strip()),
            "NERGUI_UNDUR_RAW_ROOT_FOLDER_ID": bool(raw_root_folder_id),
            "DJI_API_KEY": bool(os.environ.get("DJI_API_KEY", "").strip()),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def step_summary(summary: dict) -> str:
    """Render the sync outcome as markdown, counts only.

    This goes to the Actions run summary, so it must name no Drive file or
    folder. Only whether a secret is present is reported, never its value.
    """
    lines = ["## Drive sync", ""]
    secrets = summary.get("secretsConfigured", {})
    lines.append("| Secret | Configured |")
    lines.append("| --- | --- |")
    for name, present in sorted(secrets.items()):
        lines.append(f"| `{name}` | {'yes' if present else '**no**'} |")
    nergui = summary.get("nerguiUndurMagArrow")
    lines += ["", "### Nergui Undur MagArrow", ""]
    if nergui:
        for key in (
            "discoveredSourceCount", "chosenAcquisitionCount", "csvSourceCount", "textSourceCount",
            "magdataSourceCount", "retainedCount", "downloadedCount", "importedCount",
            "updatedCount", "skippedOversizeCount", "failedCount",
            "publishedAcquisitionCount", "publishedDateCount", "dateFrom", "dateTo",
        ):
            lines.append(f"- {key}: `{nergui.get(key)}`")
    else:
        lines.append("- not run (secret missing or discovery failed)")
    archive_summary = summary.get("nerguiUndurMagArrowArchive")
    lines += ["", "### Nergui Undur MagArrow archive", ""]
    if archive_summary:
        for key in ("downloadedFileCount", "publishedLineCount", "actualTrackSourceCount", "dateFrom", "dateTo"):
            lines.append(f"- {key}: `{archive_summary.get(key)}`")
    else:
        lines.append("- not updated; the previously published asset was preserved")
    lines += ["", "### Project-root discovery", ""]
    discoveries = summary.get("projectRootDroneSources", {})
    if discoveries:
        for project_key, discovery in sorted(discoveries.items()):
            lines.append(f"#### {project_key}")
            for key in ("matchedBranchCount", "scannedFolderCount", "inspectedFileCount",
                        "headerProbesUsed", "coordinateSourceCount", "dateFrom", "dateTo"):
                lines.append(f"- {key}: `{discovery.get(key)}`")
            for label in ("sourceCountByType", "discoveryDecisions"):
                if discovery.get(label):
                    lines.append(f"- {label}: `{json.dumps(discovery[label], ensure_ascii=False)}`")
    else:
        lines.append("- not run (no configured root was readable)")
    lines += ["", "### Per project", "",
              "| Project | Tracker rows | Folders scanned | Unreadable | Candidates | Downloaded | Tracker tracks | Campaign segments |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for key, result in sorted(summary.get("projects", {}).items()):
        stage = result.get("stages", {})
        lines.append(
            f"| {key} | {result.get('trackerMissionRows', 0)} | {stage.get('missionFoldersScanned', 0)} "
            f"| {stage.get('missionFoldersUnreadable', 0)} | {stage.get('candidateFilesFound', 0)} "
            f"| {stage.get('sourcesDownloaded', 0)} | {result.get('importReport', {}).get('imported', 0)} "
            f"| {result.get('campaignImportReport', {}).get('segments', 0)} |"
        )
    lines += ["", "### Reason codes", ""]
    for key, result in sorted(summary.get("projects", {}).items()):
        merged: dict[str, int] = {}
        for source in (result.get("reasons", {}),
                       result.get("importReport", {}).get("reasonCounts", {}),
                       result.get("campaignImportReport", {}).get("reasonCounts", {})):
            for code, count in (source or {}).items():
                merged[code] = merged.get(code, 0) + count
        lines.append(f"- **{key}**: `{json.dumps(merged, ensure_ascii=False) if merged else '{}'}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--step-summary", type=Path, help="Write a counts-only markdown summary here.")
    args = parser.parse_args()
    summary = sync(args.config.resolve(), args.repo.resolve(), args.workspace.resolve())
    if args.step_summary:
        with args.step_summary.open("a", encoding="utf-8") as handle:
            handle.write(step_summary(summary))


if __name__ == "__main__":
    main()
