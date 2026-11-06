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
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coordinate_sources as cs  # noqa: E402
import export_magdata_trajectories as magarrow  # noqa: E402
import export_magarrow_planned_lines as magarrow_plans  # noqa: E402
import import_project_flight_tracks as tracker_import  # noqa: E402


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
# Converted exports appear as both "SRVY0-ACQU44-10Hz.csv" and
# "SRVY0-ACQU10_10Hz.csv" depending on who ran the MagArrow converter.
MAGARROW_SOURCE_RE = re.compile(
    r"SRVY\d+-ACQU\d+(?:\s*\(\d+\))?(?:[-_ ]?10\s*Hz)?\.(?:csv|txt|magdata)$",
    re.IGNORECASE,
)
MAGARROW_SOURCE_PRIORITY = {".csv": 0, ".txt": 1, ".magdata": 2}
# A converted CSV and its raw .magdata start within a second of each other.
# Distinct flights that reuse an acquisition number start minutes apart.
MAGARROW_SAME_FLIGHT_SECONDS = 300
# A trajectory that touches no licence polygon is still attributed to the
# nearest licence when it lies this close (tie lines, turn-arounds).
MAGARROW_LICENCE_MAX_DISTANCE_M = 10_000
MAGARROW_SWATH_WIDTH_M = 50
NERGUI_LICENCE = "XV-023099"
METRES_PER_DEGREE = 111_320.0
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


def tracker_rows(path: Path, project_key: str, include_unlinked: bool = False) -> list[dict]:
    """Checklist mission rows; by default only those with a folder hyperlink.

    Most crews stop filling the Link column after the first weeks, so the
    Drive-wide sidecar search needs every mission row (``include_unlinked``).
    """
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
        if not linked_folder and not include_unlinked:
            continue
        rows.append({
            "trackerId": f"{project_key}:{row_number}",
            "mission": mission,
            "date": current_date,
            "sensor": clean(sheet.cell(row_number, 4).value).upper(),
            "folderId": linked_folder,
        })
    return rows


def recent_tracker_rows(rows: list[dict], lookback_days: int) -> list[dict]:
    """Keep the latest rolling window for expensive linked-folder scans.

    Project-root discovery still inventories historical coordinate sources.
    The tracker hyperlinks are the reliable incremental path for new missions,
    so the window is anchored to each tracker workbook's newest date rather
    than to today's date (important for seasonal projects).
    """
    dated = []
    for row in rows:
        try:
            dated.append(datetime.strptime(row.get("date") or "", "%Y-%m-%d").date())
        except ValueError:
            continue
    if not dated or lookback_days < 0:
        return list(rows)
    cutoff = max(dated) - timedelta(days=lookback_days)
    selected = []
    for row in rows:
        try:
            row_date = datetime.strptime(row.get("date") or "", "%Y-%m-%d").date()
        except ValueError:
            # An undated linked mission cannot be assigned to a safe rolling
            # window, so retain it for explicit validation instead of hiding it.
            selected.append(row)
            continue
        if row_date >= cutoff:
            selected.append(row)
    return selected


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


def list_coordinate_children(service, parent_id: str) -> list[dict]:
    """Find lightweight coordinate sidecars without listing mission imagery."""
    request = service.files().list(
        q=(
            f"'{parent_id}' in parents and trashed = false and ("
            "name = 'metadata.csv' or name contains 'Timestamp.MRK' or "
            "name contains 'DJIFlightRecord')"
        ),
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
            elif MAGARROW_SOURCE_RE.search(name):
                # File content is validated after download. Restricting this
                # archive fast path to explicit acquisition names avoids
                # probing unrelated Proton/DJI text logs and extra Drive calls.
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
    # Recent linked missions commonly contain thousands of images alongside a
    # generated metadata.csv. Query that exact lightweight sidecar first so a
    # daily sync does not enumerate every photo merely to rediscover it.
    metadata_sources = [
        resolve_shortcut(service, item)
        for item in list_named_children(service, root_folder_id, DJI_METADATA_FILE)
    ]
    metadata_sources = [
        item for item in metadata_sources
        if item.get("mimeType") != FOLDER_MIME
    ]
    if metadata_sources:
        return metadata_sources

    # Older/unprocessed folders may lack metadata.csv. Enumerate those once so
    # MRK/KML sources or image EXIF GPS can still provide a verified fallback.
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


def magarrow_source_id_hash(item: dict) -> str:
    """Non-reversible identity of one Drive file that survives content edits."""
    return hashlib.sha256(f"drive-file:{item.get('id') or ''}".encode("utf-8")).hexdigest()[:24]


def magarrow_priority(name: str | None) -> int:
    return MAGARROW_SOURCE_PRIORITY.get(Path(name or "").suffix.lower(), 9)


def base_acquisition(value: str | None) -> str:
    """Acquisition name without a Drive copy suffix such as "(1)"."""
    return re.sub(r"\s*\(\d+\)$", "", clean(value)).upper()


def parse_iso_time(value) -> datetime | None:
    text = clean(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def same_magarrow_flight(left: dict, right: dict) -> bool:
    """Whether two trajectories are copies of one MagArrow acquisition.

    Acquisition numbers restart and Drive appends "(1)" to re-uploads, so the
    name alone is not identity: the flights must also start together.
    """
    left_props = left.get("properties", {})
    right_props = right.get("properties", {})
    if base_acquisition(left_props.get("acquisition")) != base_acquisition(right_props.get("acquisition")):
        return False
    left_start = parse_iso_time(left_props.get("startTime"))
    right_start = parse_iso_time(right_props.get("startTime"))
    if left_start and right_start:
        try:
            return abs((left_start - right_start).total_seconds()) <= MAGARROW_SAME_FLIGHT_SECONDS
        except TypeError:
            pass
    return clean(left_props.get("date")) == clean(right_props.get("date"))


def _unique(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def feature_fingerprints(feature: dict) -> list[str]:
    props = feature.get("properties", {})
    return _unique([props.get("sourceFingerprint"), *(props.get("sourceFingerprints") or [])])


def feature_id_hashes(feature: dict) -> list[str]:
    props = feature.get("properties", {})
    return _unique([props.get("sourceIdHash"), *(props.get("sourceIdHashes") or [])])


def remember_magarrow_source(feature: dict, fingerprint: str | None, id_hash: str | None) -> None:
    """Record an alternative copy of the same flight so it is never re-downloaded."""
    props = feature.setdefault("properties", {})
    props["sourceFingerprints"] = _unique([*feature_fingerprints(feature), fingerprint])
    props["sourceIdHashes"] = _unique([*feature_id_hashes(feature), id_hash])


MAGARROW_SOURCE_KEYS = (
    "startTime", "endTime", "sourceFile", "sourceFingerprint", "sourceIdHash", "pointCount", "nmeaSource",
)


def adopt_magarrow_source(target: dict, source: dict) -> None:
    """Give ``target`` the geometry and provenance of a better copy of its flight.

    The published identity (id, date, acquisition, project) is kept, so a map
    link or coverage date never jumps because a CSV replaced a raw container.
    """
    fingerprints = _unique([*feature_fingerprints(target), *feature_fingerprints(source)])
    id_hashes = _unique([*feature_id_hashes(target), *feature_id_hashes(source)])
    target["geometry"] = source["geometry"]
    target_props = target.setdefault("properties", {})
    source_props = source.get("properties", {})
    for key in MAGARROW_SOURCE_KEYS:
        if source_props.get(key) is not None:
            target_props[key] = source_props[key]
        else:
            target_props.pop(key, None)
    target_props["sourceFingerprints"] = fingerprints
    target_props["sourceIdHashes"] = id_hashes
    if target_props.get("projectKey"):
        target_props["sourceKind"] = magarrow_source_kind(target_props.get("sourceFile"))


def magarrow_source_kind(source_file: str | None) -> str:
    return {
        ".csv": "MagArrow 10 Hz CSV",
        ".txt": "MagArrow coordinate table",
        ".magdata": "MagArrow raw .magdata (embedded NMEA)",
    }.get(Path(source_file or "").suffix.lower(), "MagArrow trajectory")


def nearest_path_date(path: str | None) -> str | None:
    """Date of the deepest dated folder, so a dated child beats a ranged parent."""
    parts = [part for part in re.split(r"[/\\]+", path or "") if part]
    for part in reversed(parts[:-1]):
        found = source_date(part)
        if found:
            return found
    return source_date_from_path(path or "")


def settle_magarrow_date(feature: dict, path_day: str | None) -> bool:
    """Use the local field-folder date when it agrees with the GNSS date.

    MagArrow timestamps are UTC while Drive folders use the local survey day,
    so a one-day difference is expected for early-morning flights. A larger
    gap means the file was filed under the wrong day; the GNSS date wins.
    Returns True when the folder date had to be overruled.
    """
    props = feature.setdefault("properties", {})
    started = parse_iso_time(props.get("startTime"))
    content_day = started.date().isoformat() if started else clean(props.get("date")) or None
    chosen, overruled = content_day or path_day, False
    if path_day and content_day:
        try:
            gap = abs((date.fromisoformat(path_day) - date.fromisoformat(content_day)).days)
        except ValueError:
            gap = 0
        if gap <= 1:
            chosen = path_day
        else:
            overruled = True
    if not chosen:
        raise ValueError("MagArrow source has no verified date")
    if chosen != props.get("date"):
        props["date"] = chosen
        feature["id"] = magarrow.feature_id(props.get("acquisition") or "magarrow", chosen)
    return overruled


def load_licence_index(path: Path) -> list[dict]:
    """Licence polygons used to attribute a trajectory to a project."""
    if not path.exists():
        return []
    licences = []
    for feature in json.loads(path.read_text(encoding="utf-8")).get("features", []):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        code = clean(props.get("LICENSE"))
        points = list(tracker_import.iter_coordinate_pairs(geometry.get("coordinates")))
        if not code or not points or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        name = clean(props.get("AREANAME")) or code
        licences.append({
            "code": code,
            "name": name,
            "label": clean(props.get("AREANAME_L")) or name,
            "geometry": geometry,
            "bbox": (
                min(point[0] for point in points), min(point[1] for point in points),
                max(point[0] for point in points), max(point[1] for point in points),
            ),
        })
    return licences


def _distance_to_bbox_m(point: tuple[float, float], bbox) -> float:
    lon, lat = point
    west, south, east, north = bbox
    nearest_lon = min(max(lon, west), east)
    nearest_lat = min(max(lat, south), north)
    dx = (lon - nearest_lon) * METRES_PER_DEGREE * math.cos(math.radians(lat))
    dy = (lat - nearest_lat) * METRES_PER_DEGREE
    return math.hypot(dx, dy)


def assign_licence(coordinates: list, licences: list[dict]) -> dict | None:
    """Attribute a trajectory to the licence holding most of its positions.

    Survey lines often overrun the licence boundary, so a majority is not
    required; a track outside every polygon falls back to the nearest licence
    within MAGARROW_LICENCE_MAX_DISTANCE_M, and is otherwise unassigned.
    """
    points = [(float(point[0]), float(point[1])) for point in coordinates or [] if len(point) >= 2]
    if not points or not licences:
        return None
    best, best_inside = None, 0
    for licence in licences:
        west, south, east, north = licence["bbox"]
        inside = sum(
            1 for lon, lat in points
            if west <= lon <= east and south <= lat <= north
            and magarrow.point_in_geometry((lon, lat), licence["geometry"])
        )
        if inside > best_inside:
            best, best_inside = licence, inside
    if best:
        return best
    centre = (sum(lon for lon, _ in points) / len(points), sum(lat for _, lat in points) / len(points))
    nearest = min(licences, key=lambda licence: _distance_to_bbox_m(centre, licence["bbox"]))
    return nearest if _distance_to_bbox_m(centre, nearest["bbox"]) <= MAGARROW_LICENCE_MAX_DISTANCE_M else None


def magarrow_project_for_licence(licence: dict, config_projects: list[dict] | None = None) -> dict:
    """Project identity for a licence: a configured tracker project, else the licence itself."""
    code = licence["code"]
    for project in config_projects or []:
        if clean(project.get("licence")) == code and project.get("key"):
            known = tracker_import.PROJECTS.get(project["key"], {})
            return {
                "key": project["key"],
                "project": clean(project.get("projectName")) or known.get("project") or licence["name"],
                "area": clean(project.get("area")) or known.get("area") or licence["label"],
                "licence": code,
            }
    for key, value in tracker_import.PROJECTS.items():
        if value["licence"] == code:
            return {"key": key, "project": value["project"], "area": value["area"], "licence": code}
    slug = re.sub(r"[^a-z0-9]+", "-", code.lower()).strip("-")
    return {"key": f"licence-{slug}", "project": licence["name"], "area": licence["label"], "licence": code}


def is_standalone_magarrow(feature: dict) -> bool:
    props = feature.get("properties", {})
    return props.get("sensor") == "MagArrow" and not props.get("trackerId")


def project_magarrow_feature(feature: dict, project: dict) -> dict:
    """Publish a non-Nergui MagArrow trajectory in the multi-project track layer."""
    props = feature.get("properties", {})
    acquisition = props.get("acquisition") or "MagArrow"
    day = props.get("date")
    slug = re.sub(r"[^a-z0-9]+", "-", f"{day}-{acquisition}".lower()).strip("-")
    published = {
        "project": project["project"], "projectKey": project["key"],
        "area": project["area"], "licence": project["licence"],
        "sensor": "MagArrow", "mission": acquisition, "acquisition": acquisition, "date": day,
        "startTime": props.get("startTime"), "endTime": props.get("endTime"),
        "dataType": "actual_flight_track", "plannedActual": "actual", "status": "confirmed_source",
        "contextOnly": True, "trajectoryAvailable": True, "autoDiscovered": True,
        "licenceAssignment": "trajectory coordinates",
        "sourceFile": props.get("sourceFile"), "sourceKind": magarrow_source_kind(props.get("sourceFile")),
        "nmeaSource": props.get("nmeaSource"),
        "sourceCrs": "EPSG:4326", "displayCrs": "EPSG:4326", "crsVerified": True,
        "pointCount": props.get("pointCount"),
        "coverageStatus": "calculated", "coverageSwathWidthM": MAGARROW_SWATH_WIDTH_M,
        "coverageNote": "MagArrow trajectory-г 50 м зурвасаар тооцов (Нэргүй өндөртэй ижил аргачлал).",
        "sourceFingerprint": props.get("sourceFingerprint"),
        "sourceFingerprints": feature_fingerprints(feature),
        "sourceIdHash": props.get("sourceIdHash"),
        "sourceIdHashes": feature_id_hashes(feature),
    }
    return {
        "type": "Feature",
        "id": f"magarrow-{project['key']}-{slug}",
        "properties": {key: value for key, value in published.items() if value not in (None, [])},
        "geometry": feature["geometry"],
    }


def find_nergui_magarrow_sources(service, root_folder_id: str, max_depth: int) -> list[dict]:
    """Find named MagArrow CSV/TXT sources, with raw .magdata as fallback."""
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


def drive_search_magarrow_sources(service, path_depth: int = 4) -> tuple[list[dict], dict]:
    """Find MagArrow sources anywhere the service account can read.

    The MagArrow moves between licences and each crew files its data under a
    different project tree, so a configured root per licence goes stale. A
    name search across every readable drive finds new areas without setup.
    Only the nearest ``path_depth`` folder names are read, to recover the
    field-day folder date; they never leave the private job log.
    """
    found: list[dict] = []
    token = None
    incomplete = False
    while True:
        request = service.files().list(
            q=f"name contains 'SRVY' and trashed = false and mimeType != '{FOLDER_MIME}'",
            fields="nextPageToken,incompleteSearch,files(id,name,mimeType,size,modifiedTime,md5Checksum,parents)",
            pageSize=1000,
            pageToken=token,
            corpora="allDrives",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        response = execute_with_retry(request)
        incomplete = incomplete or bool(response.get("incompleteSearch"))
        found.extend(
            item for item in response.get("files", [])
            if MAGARROW_SOURCE_RE.search(item.get("name", ""))
        )
        token = response.get("nextPageToken")
        if not token:
            break

    folders: dict[str, tuple[str | None, str | None]] = {}

    def ancestor_names(folder_id: str | None) -> list[str]:
        names = []
        current = folder_id
        for _ in range(path_depth):
            if not current:
                break
            if current not in folders:
                try:
                    meta = execute_with_retry(service.files().get(
                        fileId=current, fields="id,name,parents", supportsAllDrives=True,
                    ))
                    folders[current] = (meta.get("name", ""), (meta.get("parents") or [None])[0])
                except Exception:
                    folders[current] = (None, None)
            name, parent = folders[current]
            if name is None:
                break
            names.append(name)
            current = parent
        return list(reversed(names))

    for item in found:
        parents = item.get("parents") or []
        item["relativePath"] = "/".join([*ancestor_names(parents[0] if parents else None), item.get("name", "")])
        item["discoveredBy"] = "drive-search"
    summary = {
        "sourceCount": len(found),
        "folderLookups": len(folders),
        "incompleteSearch": incomplete,
    }
    return found, summary


DJI_TIMESTAMP_MRK_RE = re.compile(r"^(?P<mission>.+?)[_ -]Timestamp\.MRK$", re.IGNORECASE)


def dji_mission_key(value: str | None) -> str:
    return comparable_name(clean(value))


def drive_search_dji_sidecars(service) -> tuple[dict[str, list[dict]], dict]:
    """Index every readable DJI ``<mission>_Timestamp.MRK`` by its mission name.

    DJI names the camera-event log after the mission, so it is matched to a
    tracker row by name alone, wherever the crew stored it: another shared
    drive, a row without a folder link, or a mission older than any lookback.
    """
    index: dict[str, list[dict]] = {}
    token = None
    count = 0
    incomplete = False
    while True:
        request = service.files().list(
            q=f"name contains 'Timestamp' and trashed = false and mimeType != '{FOLDER_MIME}'",
            fields="nextPageToken,incompleteSearch,files(id,name,mimeType,size,modifiedTime,md5Checksum,parents)",
            pageSize=1000,
            pageToken=token,
            corpora="allDrives",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        response = execute_with_retry(request)
        incomplete = incomplete or bool(response.get("incompleteSearch"))
        for item in response.get("files", []):
            match = DJI_TIMESTAMP_MRK_RE.match(item.get("name", ""))
            if not match:
                continue
            key = dji_mission_key(match.group("mission"))
            if key:
                index.setdefault(key, []).append({**item, "discoveredBy": "drive-search"})
                count += 1
        token = response.get("nextPageToken")
        if not token:
            break
    return index, {"sourceCount": count, "missionCount": len(index), "incompleteSearch": incomplete}


def choose_dji_sidecar(service, candidates: list[dict], folder_names: dict[str, str]) -> dict:
    """Pick one MRK when a mission was copied to several folders.

    Copies left in a folder marked for deletion are only used as a last resort.
    """
    def parent_name(item: dict) -> str:
        parents = item.get("parents") or []
        if not parents:
            return ""
        parent = parents[0]
        if parent not in folder_names:
            try:
                folder_names[parent] = execute_with_retry(service.files().get(
                    fileId=parent, fields="id,name", supportsAllDrives=True,
                )).get("name", "")
            except Exception:
                folder_names[parent] = ""
        return folder_names[parent]

    if len(candidates) == 1:
        return candidates[0]

    def size(item: dict) -> int:
        try:
            return int(item.get("size") or 0)
        except (TypeError, ValueError):
            return 0

    # Newest first, then (stable) prefer kept folders and the fuller log.
    ordered = sorted(candidates, key=lambda item: (item.get("modifiedTime", ""), item.get("id", "")), reverse=True)
    ordered.sort(key=lambda item: (
        bool(re.search(r"delet|устга|trash", parent_name(item), re.IGNORECASE)),
        -size(item),
    ))
    return ordered[0]


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


def find_drone_sources(
    service,
    root_folder_id: str,
    discovery_depth: int = 5,
    scan_depth: int = 8,
    mission_lookback_days: int = 21,
    magarrow_sink: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    """Return strong coordinate-source candidates without exposing private paths in the summary.

    MagArrow acquisitions met during the scan are also appended to
    ``magarrow_sink`` so the all-licence MagArrow sync can use them even when
    the Drive-wide search cannot see this root.
    """
    roots = find_drone_source_roots(service, root_folder_id, discovery_depth)
    found = []
    seen_files = set()
    scanned_folders = 0
    inspected_files = 0
    probe_budget = [MAX_HEADER_PROBES]
    discovery_reasons: dict[str, int] = {}
    mission_folders: list[dict] = []

    def consider(item: dict, path: str) -> None:
        nonlocal inspected_files
        if item.get("id") in seen_files:
            return
        inspected_files += 1
        if magarrow_sink is not None and MAGARROW_SOURCE_RE.search(item.get("name", "")):
            magarrow_sink.append({**item, "relativePath": path, "discoveredBy": "project-root"})
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
                        # Keep their identity now, then query only lightweight
                        # coordinate sidecars for the latest rolling window.
                        mission_folders.append({**item, "relativePath": path})
                        continue
                    if depth < scan_depth:
                        queue.append((item["id"], path, depth + 1))
                    continue
                consider(item, path)

    mission_dates = [
        source_date_from_path(item.get("relativePath", ""))
        for item in mission_folders
    ]
    mission_dates = [value for value in mission_dates if value]
    mission_cutoff = None
    if mission_dates and mission_lookback_days >= 0:
        latest = datetime.strptime(max(mission_dates), "%Y-%m-%d").date()
        mission_cutoff = (latest - timedelta(days=mission_lookback_days)).isoformat()
    recent_missions = []
    for mission in mission_folders:
        mission_date = source_date_from_path(mission.get("relativePath", ""))
        if mission_cutoff and mission_date and mission_date < mission_cutoff:
            continue
        recent_missions.append(mission)
        direct = list_coordinate_children(service, mission["id"])
        for raw_item in direct:
            item = resolve_shortcut(service, raw_item)
            consider(item, f"{mission['relativePath']}/{item.get('name', '')}")
        for metadata_folder in list_named_children(service, mission["id"], "Metadata"):
            if metadata_folder.get("mimeType") != FOLDER_MIME:
                continue
            for raw_item in list_coordinate_children(service, metadata_folder["id"]):
                item = resolve_shortcut(service, raw_item)
                consider(
                    item,
                    f"{mission['relativePath']}/Metadata/{item.get('name', '')}",
                )
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
        "recentMissionFolderCount": len(recent_missions),
        "missionFolderCutoff": mission_cutoff,
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
    metadata_matches = [
        item for item in eligible
        if Path(item.get("name", "")).name.lower() == DJI_METADATA_FILE
    ]
    if metadata_matches:
        # metadata.csv is generated inside one already-linked mission folder;
        # its generic filename therefore does not need the mission token.
        eligible = metadata_matches
    elif named_matches:
        eligible = named_matches

    # Prefer a newly modified file within the best supported source type.
    eligible.sort(key=lambda item: item.get("modifiedTime", ""), reverse=True)
    eligible.sort(key=lambda item: (
        0 if Path(item.get("name", "")).name.lower() == DJI_METADATA_FILE else 1,
        SOURCE_PRIORITY.get(Path(item.get("name", "")).suffix.lower(), 99),
    ))
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


def parse_magarrow_source(
    item: dict,
    local_path: Path,
    source_dir: Path,
    raw_parsed_dir: Path,
    path_day: str | None,
    fingerprint: str,
) -> dict:
    """Parse one downloaded MagArrow source into a trajectory feature."""
    source_name = item.get("name", "")
    lower = source_name.lower()
    if lower.endswith(".csv"):
        try:
            return magarrow.parse_10hz_csv(
                local_path,
                source_name=source_name,
                source_fingerprint=fingerprint,
            )
        except ValueError:
            return magarrow.parse_coordinate_table(
                local_path,
                source_name=source_name,
                fallback_date=path_day,
                source_fingerprint=fingerprint,
            )
    if lower.endswith(".txt"):
        return magarrow.parse_coordinate_table(
            local_path,
            source_name=source_name,
            fallback_date=path_day,
            source_fingerprint=fingerprint,
        )
    day = path_day or magarrow.magdata_date(local_path)
    if not day:
        raise ValueError("raw MagArrow source has no verified date")
    dated_path = source_dir / f"{day}__{safe_filename(source_name)}"
    local_path.replace(dated_path)
    # parse_file writes its intermediate JSON. Keep that staging output
    # separate from the final parsed directory, which holds exactly one
    # canonical feature per flight; mixing them duplicates raw acquisitions.
    parsed_path = magarrow.parse_file(dated_path, raw_parsed_dir)
    feature = json.loads(parsed_path.read_text(encoding="utf-8"))
    feature.setdefault("properties", {}).update({
        "sourceFile": source_name,
        "sourceFingerprint": fingerprint,
    })
    return feature


def _identity_rank(entry: dict) -> int:
    """Lower is a better published identity for a flight with several copies.

    A copy filed under another day's folder carries that wrong date, so the
    copy whose date agrees with its own GNSS start wins first; then the name
    without a Drive "(1)" suffix; then the canonical dated feature id.
    """
    feature = entry["feature"]
    props = feature.get("properties", {})
    rank = 0
    started = parse_iso_time(props.get("startTime"))
    try:
        if started and abs((date.fromisoformat(clean(props.get("date"))) - started.date()).days) > 1:
            rank += 4
    except ValueError:
        rank += 4
    acquisition = clean(props.get("acquisition")).upper()
    if base_acquisition(acquisition) != acquisition:
        rank += 2
    if feature.get("id") != magarrow.feature_id(props.get("acquisition") or "", props.get("date")):
        rank += 1
    return rank


def collapse_magarrow_duplicates(entries: list[dict]) -> tuple[list[dict], int]:
    """Merge published copies of one flight (raw container + converted CSV)."""
    kept: list[dict] = []
    collapsed = 0
    for entry in entries:
        index = next(
            (position for position, item in enumerate(kept)
             if same_magarrow_flight(item["feature"], entry["feature"])),
            None,
        )
        if index is None:
            kept.append(entry)
            continue
        collapsed += 1
        keep, other = kept[index], entry
        if _identity_rank(other) < _identity_rank(keep):
            keep, other = other, keep
        kept[index] = keep
        other_feature = other["feature"]
        if magarrow_priority(other_feature.get("properties", {}).get("sourceFile")) < magarrow_priority(
            keep["feature"].get("properties", {}).get("sourceFile")
        ):
            adopt_magarrow_source(keep["feature"], other_feature)
        else:
            props = keep["feature"].setdefault("properties", {})
            props["sourceFingerprints"] = _unique([
                *feature_fingerprints(keep["feature"]), *feature_fingerprints(other_feature),
            ])
            props["sourceIdHashes"] = _unique([
                *feature_id_hashes(keep["feature"]), *feature_id_hashes(other_feature),
            ])
    return kept, collapsed


def sync_magarrow_tracks(
    service,
    sources: list[dict],
    repo: Path,
    workspace: Path,
    max_size: int,
    config_projects: list[dict] | None = None,
) -> dict:
    """Import MagArrow trajectories from every licence and publish them per project.

    Each Drive file is downloaded at most once: every published trajectory
    remembers the hashed fingerprints of all copies of its flight. A new
    trajectory is attributed to a licence from its own coordinates. Nergui
    Undur keeps its dedicated layer and uchastik coverage; every other licence
    is published in the multi-project track layer with the MagArrow sensor.
    """
    sources = list({item["id"]: item for item in sources if item.get("id")}.values())
    data_dir = repo / "dist" / "data"
    tracks_path = data_dir / "magarrow" / "actual-tracks.geojson"
    coverage_path = data_dir / "magarrow" / "coverage-stats.json"
    registry_path = data_dir / "datasets.json"
    project_tracks_path = data_dir / "context" / "project-flight-tracks.geojson"
    licences = load_licence_index(data_dir / "context" / "licenses.geojson")

    nergui_payload = json.loads(tracks_path.read_text(encoding="utf-8"))
    project_payload = (
        json.loads(project_tracks_path.read_text(encoding="utf-8"))
        if project_tracks_path.exists() else None
    )
    entries = [{"feature": feature, "collection": "nergui"} for feature in nergui_payload.get("features", [])]
    if project_payload:
        entries.extend(
            {"feature": feature, "collection": "project"}
            for feature in project_payload.get("features", [])
            if is_standalone_magarrow(feature)
        )
    entries, collapsed = collapse_magarrow_duplicates(entries)
    known_fingerprints = {fp for entry in entries for fp in feature_fingerprints(entry["feature"])}
    owner_by_id_hash = {
        id_hash: entry for entry in entries for id_hash in feature_id_hashes(entry["feature"])
    }

    source_dir = workspace / "magarrow-sources"
    parsed_dir = workspace / "nergui-magarrow-parsed"
    raw_parsed_dir = workspace / "nergui-magarrow-raw-staging"
    for folder in (source_dir, parsed_dir, raw_parsed_dir):
        folder.mkdir(parents=True, exist_ok=True)

    candidates = [item for item in sources if magarrow_acquisition_key(item.get("name", ""))]
    summary = {
        "discoveredSourceCount": len(sources),
        "chosenAcquisitionCount": len(candidates),
        "csvSourceCount": sum(1 for item in candidates if item.get("name", "").lower().endswith(".csv")),
        "textSourceCount": sum(1 for item in candidates if item.get("name", "").lower().endswith(".txt")),
        "magdataSourceCount": sum(1 for item in candidates if item.get("name", "").lower().endswith(".magdata")),
        "retainedCount": 0,
        "alternateSourceCount": 0,
        "downloadedCount": 0,
        "importedCount": 0,
        "updatedCount": 0,
        "skippedOversizeCount": 0,
        "failedCount": 0,
        "unassignedCount": 0,
        "pathDateOverruledCount": 0,
        "duplicateCollapsedCount": collapsed,
        "licenceIndexCount": len(licences),
    }

    def entry_priority(entry: dict) -> int:
        return magarrow_priority(entry["feature"].get("properties", {}).get("sourceFile"))

    # Converted CSVs first: once a flight has its preferred source, the raw
    # container of the same flight can be recognised without downloading it.
    ordered = sorted(candidates, key=lambda item: (
        magarrow_priority(item.get("name")),
        nearest_path_date(item.get("relativePath", "")) or "9999-99-99",
        item.get("relativePath", item.get("name", "")),
    ))
    for item in ordered:
        source_name = item.get("name", "")
        acquisition = magarrow_acquisition_key(source_name)
        fingerprint = magarrow_source_fingerprint(item)
        if fingerprint in known_fingerprints:
            summary["retainedCount"] += 1
            continue
        id_hash = magarrow_source_id_hash(item)
        priority = magarrow_priority(source_name)
        path_day = nearest_path_date(item.get("relativePath", ""))
        owner = owner_by_id_hash.get(id_hash)
        if owner is None and path_day:
            twins = [
                entry for entry in entries
                if clean(entry["feature"].get("properties", {}).get("date")) == path_day
                and clean(entry["feature"].get("properties", {}).get("acquisition")).upper() == acquisition
            ]
            if twins and all(
                feature_id_hashes(entry["feature"]) and entry_priority(entry) <= priority
                for entry in twins
            ):
                remember_magarrow_source(twins[0]["feature"], fingerprint, id_hash)
                known_fingerprints.add(fingerprint)
                owner_by_id_hash[id_hash] = twins[0]
                summary["alternateSourceCount"] += 1
                continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > max_size:
            summary["skippedOversizeCount"] += 1
            continue
        local_path = source_dir / f"{item['id']}__{safe_filename(source_name)}"
        try:
            download_file(service, item, local_path)
            summary["downloadedCount"] += 1
            feature = parse_magarrow_source(item, local_path, source_dir, raw_parsed_dir, path_day, fingerprint)
            feature.setdefault("properties", {})["sourceIdHash"] = id_hash
            if settle_magarrow_date(feature, path_day):
                summary["pathDateOverruledCount"] += 1
        except Exception as error:
            summary["failedCount"] += 1
            status = getattr(getattr(error, "resp", None), "status", None)
            print(
                "::warning::MagArrow source import failed "
                f"(acquisition={acquisition}, error={type(error).__name__}, status={status})"
            )
            continue
        known_fingerprints.add(fingerprint)
        match = owner or next(
            (entry for entry in entries if same_magarrow_flight(entry["feature"], feature)),
            None,
        )
        if match is not None:
            match_props = match["feature"].get("properties", {})
            legacy = not match_props.get("sourceIdHash")
            replaces = (
                priority < entry_priority(match)
                or match_props.get("sourceIdHash") == id_hash
                or (legacy and priority <= entry_priority(match))
            )
            if replaces:
                adopt_magarrow_source(match["feature"], feature)
                summary["updatedCount"] += 1
            else:
                remember_magarrow_source(match["feature"], fingerprint, id_hash)
                summary["alternateSourceCount"] += 1
            owner_by_id_hash[id_hash] = match
            continue
        feature["properties"]["sourceFingerprints"] = [fingerprint]
        feature["properties"]["sourceIdHashes"] = [id_hash]
        licence = assign_licence(feature.get("geometry", {}).get("coordinates"), licences)
        if licences and licence is None:
            summary["unassignedCount"] += 1
            print(f"::notice::MagArrow acquisition {acquisition} lies outside every known licence; not published")
            continue
        if licence is None or licence["code"] == NERGUI_LICENCE:
            entry = {"feature": feature, "collection": "nergui"}
        else:
            project = magarrow_project_for_licence(licence, config_projects)
            entry = {"feature": project_magarrow_feature(feature, project), "collection": "project"}
        entries.append(entry)
        owner_by_id_hash[id_hash] = entry
        summary["importedCount"] += 1

    summary["partialImport"] = bool(summary["failedCount"] or summary["skippedOversizeCount"])
    if summary["partialImport"]:
        # Publish every successfully parsed acquisition. A transient Drive,
        # download or parser failure must not discard the rest; sources that
        # failed have no remembered fingerprint and are retried next run.
        print(
            "::warning::MagArrow sync completed partially "
            f"(failed={summary['failedCount']}, oversize={summary['skippedOversizeCount']})"
        )

    nergui_features = [entry["feature"] for entry in entries if entry["collection"] == "nergui"]
    for feature in nergui_features:
        feature.get("properties", {}).pop("sourceUrl", None)
        magarrow.write_feature(feature, parsed_dir)
    # The published base layers use plural filenames. Keep these paths aligned
    # with the repository rather than the singular names used by early fixtures.
    magarrow.build(
        parsed_dir,
        data_dir / "base" / "licenses.geojson",
        data_dir / "base" / "uchastics.geojson",
        tracks_path,
        coverage_path,
    )

    project_features = sorted(
        (entry["feature"] for entry in entries if entry["collection"] == "project"),
        key=lambda feature: (
            feature["properties"].get("projectKey", ""),
            feature["properties"].get("date", ""),
            feature["id"],
        ),
    )
    if project_payload is not None or project_features:
        payload = project_payload or {
            "type": "FeatureCollection", "name": "Verified project flight trajectories",
            "project": "Multi-project operations", "scope": "project_operations",
            "dataType": "actual_flight_track", "featureCount": 0, "features": [],
        }
        payload["features"] = [
            feature for feature in payload.get("features", []) if not is_standalone_magarrow(feature)
        ] + project_features
        payload["featureCount"] = len(payload["features"])
        project_tracks_path.parent.mkdir(parents=True, exist_ok=True)
        project_tracks_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8",
        )

    nergui_dates = sorted({
        feature.get("properties", {}).get("date")
        for feature in nergui_features
        if feature.get("properties", {}).get("date")
    })
    by_project: dict[str, dict] = {}
    for entry in entries:
        props = entry["feature"].get("properties", {})
        key = "nergui-undur" if entry["collection"] == "nergui" else props.get("projectKey", "unknown")
        stats = by_project.setdefault(key, {"acquisitionCount": 0, "dateTo": None})
        stats["acquisitionCount"] += 1
        if props.get("date") and (stats["dateTo"] is None or props["date"] > stats["dateTo"]):
            stats["dateTo"] = props["date"]
    summary.update({
        "publishedAcquisitionCount": len(nergui_features),
        "publishedDateCount": len(nergui_dates),
        "dateFrom": nergui_dates[0] if nergui_dates else None,
        "dateTo": nergui_dates[-1] if nergui_dates else None,
        "publishedByProject": dict(sorted(by_project.items())),
    })
    update_nergui_registry(registry_path, len(nergui_features), len(nergui_dates))
    return summary


def sync_nergui_magarrow(
    service,
    root_folder_id: str | None,
    repo: Path,
    workspace: Path,
    max_depth: int,
    max_size: int,
    additional_sources: list[dict] | None = None,
    config_projects: list[dict] | None = None,
) -> dict:
    """Tree-scan the configured Nergui root, then sync MagArrow for all licences."""
    sources = find_nergui_magarrow_sources(service, root_folder_id, max_depth) if root_folder_id else []
    sources.extend(additional_sources or [])
    return sync_magarrow_tracks(service, sources, repo, workspace, max_size, config_projects)


def run(command: list[str], cwd: Path) -> None:
    print("Running:", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def sync(config_path: Path, repo: Path, workspace: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    projects = config.get("projects", [])
    required_keys = {"hetsuu-hutul", "artsat", "buduunkhad", "nergui-undur"}
    if not required_keys.issubset({item.get("key") for item in projects}):
        raise ValueError("drive-sync-sources.json must configure the four core project trackers")
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
    root_magarrow_sources: list[dict] = []
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
                int(config.get("rootMissionLookbackDays", 21)),
                root_magarrow_sources,
            )
            project_root_sources[project_key] = sources
            project_root_summaries[project_key] = discovery
            print(f"{project_key} project-root coordinate source discovery:")
            print(json.dumps(discovery, ensure_ascii=False, indent=2))
        except Exception as error:
            print(f"::warning::{project_key} project-root discovery skipped: {error}")
    max_depth = int(config.get("folderScanDepth", 2))
    mission_lookback_days = int(config.get("missionFolderLookbackDays", 21))
    max_size = int(config.get("maxSourceSizeBytes", 100 * 1024 * 1024))
    magarrow_sources = list(magarrow_archive_tracks) + root_magarrow_sources
    magarrow_search_summary = None
    if config.get("magArrowDriveSearch", True):
        try:
            searched, magarrow_search_summary = drive_search_magarrow_sources(service)
            magarrow_sources.extend(searched)
            print("MagArrow Drive-wide source search:")
            print(json.dumps(magarrow_search_summary, ensure_ascii=False, indent=2))
            if magarrow_search_summary.get("incompleteSearch"):
                print("::warning::MagArrow Drive search reported incompleteSearch; some drives were not searched")
        except Exception as error:
            print(f"::warning::MagArrow Drive-wide search skipped ({type(error).__name__})")
    nergui_magarrow_summary = None
    if raw_root_folder_id or magarrow_sources:
        nergui_magarrow_summary = sync_nergui_magarrow(
            service,
            raw_root_folder_id or None,
            repo,
            workspace,
            int(config.get("nerguiMagArrowScanDepth", 8)),
            max_size,
            magarrow_sources,
            projects,
        )
        nergui_magarrow_summary["driveSearch"] = magarrow_search_summary
        print("MagArrow sync (all licences):")
        print(json.dumps(nergui_magarrow_summary, ensure_ascii=False, indent=2))
    dji_sidecars: dict[str, list[dict]] = {}
    dji_search_summary = None
    if config.get("djiSidecarDriveSearch", True):
        try:
            dji_sidecars, dji_search_summary = drive_search_dji_sidecars(service)
            print("DJI Timestamp.MRK Drive-wide search:")
            print(json.dumps(dji_search_summary, ensure_ascii=False, indent=2))
        except Exception as error:
            print(f"::warning::DJI sidecar Drive-wide search skipped ({type(error).__name__})")
    published_tracks_path = repo / "dist" / "data" / "context" / "project-flight-tracks.geojson"
    published_tracker_ids = {
        feature.get("properties", {}).get("trackerId")
        for feature in (
            json.loads(published_tracks_path.read_text(encoding="utf-8")).get("features", [])
            if published_tracks_path.exists() else []
        )
        if feature.get("properties", {}).get("trackerId")
    }
    sidecar_parent_names: dict[str, str] = {}
    campaigns = config.get("campaigns", [])
    workbook_paths = []
    project_results = {}

    for project in projects:
        tracker_item = metadata(service, project["trackerFileId"])
        workbook_path = workbooks_dir / project["workbookName"]
        download_file(service, tracker_item, workbook_path)
        workbook_paths.append(workbook_path)
        rows = tracker_rows(workbook_path, project["key"], include_unlinked=True)
        linked_rows = recent_tracker_rows([row for row in rows if row.get("folderId")], mission_lookback_days)
        project_source_dir = sources_dir / project["key"]
        project_source_dir.mkdir(parents=True)
        mapping_entries = []
        mapped_tracker_ids: set[str] = set()
        seen_folders = {}
        used_source_ids: set[str] = set()
        mismatched = []
        stage = {
            "missionRowsEligible": len(linked_rows),
            "missionRowsSkippedByLookback": len(rows) - len(linked_rows),
            "missionFoldersScanned": 0,
            "missionFoldersUnreadable": 0,
            "candidateFilesFound": 0,
            "sourcesChosen": 0,
            "sourcesDownloaded": 0,
            "downloadFailures": 0,
            "flightRecordsAwaitingApiKey": 0,
            "trackerRowsWithoutFolderLink": sum(1 for row in rows if not row.get("folderId")),
            "searchSidecarsMatched": 0,
            "searchSidecarsDownloaded": 0,
        }
        reasons: dict[str, int] = {}

        def note(code: str) -> None:
            reasons[code] = reasons.get(code, 0) + 1

        for row in linked_rows:
            # Project-root discovery is an additional inventory, not a
            # replacement for the exact mission folder linked by the tracker.
            # Root trees commonly contain only processed/campaign branches,
            # while the newest DJI/P1/L2/L3 coordinate source remains behind
            # the row hyperlink. Skipping these links made a successful daily
            # workflow silently publish stale trajectories.
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
        # Every tracker row without a published trajectory is matched by its
        # mission name to a DJI Timestamp.MRK found anywhere in Drive. This is
        # independent of folder links, lookback windows and per-project roots.
        for row in rows:
            if row["trackerId"] in mapped_tracker_ids or row["trackerId"] in published_tracker_ids:
                continue
            candidates = [
                item for item in dji_sidecars.get(dji_mission_key(row.get("mission")), [])
                if item.get("id") not in used_source_ids
            ]
            if not candidates:
                continue
            stage["searchSidecarsMatched"] += 1
            chosen = choose_dji_sidecar(service, candidates, sidecar_parent_names)
            used_source_ids.add(chosen["id"])
            local_name = f"search__{row['trackerId'].split(':')[-1].zfill(4)}__{safe_filename(chosen['name'])}"
            try:
                download_file(service, chosen, project_source_dir / local_name)
            except Exception as error:
                stage["downloadFailures"] += 1
                note(cs.ACCESS_DENIED)
                print(f"::warning::{project['key']} searched sidecar download failed ({type(error).__name__})")
                continue
            stage["searchSidecarsDownloaded"] += 1
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
            "trackerRowsWithFolders": sum(1 for row in rows if row.get("folderId")),
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
        "djiSidecarSearch": dji_search_summary,
        "secretsConfigured": {
            "ARTSAT_ROOT_FOLDER_ID": bool(os.environ.get("ARTSAT_ROOT_FOLDER_ID", "").strip()),
            "BUDUUNKHAD_ROOT_FOLDER_ID": bool(os.environ.get("BUDUUNKHAD_ROOT_FOLDER_ID", "").strip()),
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
    lines += ["", "### MagArrow (all licences)", ""]
    if nergui:
        search = nergui.get("driveSearch") or {}
        if search:
            lines.append(
                f"- driveSearch: sources `{search.get('sourceCount')}`, "
                f"incompleteSearch `{search.get('incompleteSearch')}`"
            )
        for key in (
            "discoveredSourceCount", "chosenAcquisitionCount", "csvSourceCount", "textSourceCount",
            "magdataSourceCount", "retainedCount", "alternateSourceCount", "downloadedCount",
            "importedCount", "updatedCount", "skippedOversizeCount", "failedCount",
            "unassignedCount", "pathDateOverruledCount", "duplicateCollapsedCount",
            "partialImport", "publishedAcquisitionCount", "publishedDateCount", "dateFrom", "dateTo",
        ):
            if key in nergui:
                lines.append(f"- {key}: `{nergui.get(key)}`")
        by_project = nergui.get("publishedByProject") or {}
        if by_project:
            lines += ["", "| Project | MagArrow acquisitions | Latest date |", "| --- | --- | --- |"]
            for project_key, stats in sorted(by_project.items()):
                lines.append(f"| {project_key} | {stats.get('acquisitionCount', 0)} | {stats.get('dateTo') or '—'} |")
    else:
        lines.append("- not run (secret missing or discovery failed)")
    archive_summary = summary.get("nerguiUndurMagArrowArchive")
    lines += ["", "### Nergui Undur MagArrow archive", ""]
    if archive_summary:
        for key in ("downloadedFileCount", "publishedLineCount", "actualTrackSourceCount", "dateFrom", "dateTo"):
            lines.append(f"- {key}: `{archive_summary.get(key)}`")
    else:
        lines.append("- not updated; the previously published asset was preserved")
    dji = summary.get("djiSidecarSearch")
    lines += ["", "### DJI Timestamp.MRK search (all drives)", ""]
    if dji:
        lines.append(
            f"- sources `{dji.get('sourceCount')}`, missions `{dji.get('missionCount')}`, "
            f"incompleteSearch `{dji.get('incompleteSearch')}`"
        )
    else:
        lines.append("- not run")
    lines += ["", "### Project-root discovery", ""]
    discoveries = summary.get("projectRootDroneSources", {})
    if discoveries:
        for project_key, discovery in sorted(discoveries.items()):
            lines.append(f"#### {project_key}")
            for key in ("matchedBranchCount", "scannedFolderCount", "inspectedFileCount",
                        "headerProbesUsed", "coordinateSourceCount", "recentMissionFolderCount",
                        "missionFolderCutoff", "dateFrom", "dateTo"):
                lines.append(f"- {key}: `{discovery.get(key)}`")
            for label in ("sourceCountByType", "discoveryDecisions"):
                if discovery.get(label):
                    lines.append(f"- {label}: `{json.dumps(discovery[label], ensure_ascii=False)}`")
    else:
        lines.append("- not run (no configured root was readable)")
    lines += ["", "### Per project", "",
              "| Project | Tracker rows | Folders scanned | Unreadable | Candidates | Downloaded | Tracker tracks | Campaign segments | Rows without link | MRK matched by name |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for key, result in sorted(summary.get("projects", {}).items()):
        stage = result.get("stages", {})
        lines.append(
            f"| {key} | {result.get('trackerMissionRows', 0)} | {stage.get('missionFoldersScanned', 0)} "
            f"| {stage.get('missionFoldersUnreadable', 0)} | {stage.get('candidateFilesFound', 0)} "
            f"| {stage.get('sourcesDownloaded', 0)} | {result.get('importReport', {}).get('imported', 0)} "
            f"| {result.get('campaignImportReport', {}).get('segments', 0)} "
            f"| {stage.get('trackerRowsWithoutFolderLink', 0)} | {stage.get('searchSidecarsMatched', 0)} |"
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
