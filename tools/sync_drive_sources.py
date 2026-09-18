"""Synchronise verified project flight sources from Google Drive.

This script is intended for GitHub Actions with a read-only Google Drive
service account. It downloads the three tracker workbooks, follows each
Checklist mission folder hyperlink, imports only coordinate-bearing flight
files, rebuilds coverage, and leaves git commit/push to the workflow.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook


FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPORTED_SUFFIXES = {".mrk", ".kmz", ".kml", ".csv", ".txt"}
SOURCE_PRIORITY = {".kmz": 0, ".mrk": 1, ".kml": 2, ".csv": 3, ".txt": 4}
FOLDER_ID_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
DATE_COMPACT_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
DATE_DASH_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
DJI_FILE_RE = re.compile(r"^DJI(?:[_\-\s]|$)", re.IGNORECASE)
RAW_DATA_FOLDER_KEYS = {"0rawdata", "rawdata"}
DJI_FILE_COUNT_SENSORS = {4: "P1", 11: "L3"}
DRONE_FOLDER_RE = re.compile(
    r"(?:^|[^a-z0-9])(drone|uav|magnetic[ _-]*survey|magarrow|flight[ _-]*(?:log|record))(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
DRONE_SOURCE_RE = re.compile(
    r"(?:DJIFlightRecord|Timestamp|(?:^|[_-])DJI(?:[_-]|$)|SRVY\d*[-_]ACQU\d*|flight[ _-]*(?:log|record|track)|trajectory)",
    re.IGNORECASE,
)


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


def metadata(service, file_id: str) -> dict:
    return service.files().get(
        fileId=file_id,
        fields="id,name,mimeType,size,modifiedTime,md5Checksum,shortcutDetails,webViewLink",
        supportsAllDrives=True,
    ).execute()


def list_children(service, parent_id: str) -> list[dict]:
    result = []
    token = None
    while True:
        response = service.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            fields="nextPageToken,files(id,name,mimeType,size,modifiedTime,md5Checksum,shortcutDetails,webViewLink)",
            pageSize=1000,
            pageToken=token,
            spaces="drive",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        result.extend(response.get("files", []))
        token = response.get("nextPageToken")
        if not token:
            return result


def resolve_shortcut(service, item: dict) -> dict:
    if item.get("mimeType") != SHORTCUT_MIME:
        return item
    target_id = item.get("shortcutDetails", {}).get("targetId")
    return metadata(service, target_id) if target_id else item


def find_sources(service, root_folder_id: str, max_depth: int) -> list[dict]:
    found = []
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
            if Path(item.get("name", "")).suffix.lower() in SUPPORTED_SUFFIXES:
                found.append(item)
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


def is_drone_coordinate_source(name: str) -> bool:
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return False
    if suffix == ".mrk":
        return True
    return bool(DRONE_SOURCE_RE.search(Path(name).name))


def find_drone_source_roots(service, root_folder_id: str, max_depth: int = 5) -> list[dict]:
    """Locate explicitly named drone/UAV/magnetic-survey branches under a project root."""
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
            if DRONE_FOLDER_RE.search(item.get("name", "")):
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
                    if depth < scan_depth:
                        queue.append((item["id"], path, depth + 1))
                    continue
                if item.get("id") in seen_files or not is_drone_coordinate_source(item.get("name", "")):
                    continue
                seen_files.add(item["id"])
                found.append({**item, "relativePath": path, "sourceDate": source_date_from_path(path)})
    dated = sorted(item["sourceDate"] for item in found if item.get("sourceDate"))
    suffix_counts = {}
    for item in found:
        suffix = Path(item.get("name", "")).suffix.lower().lstrip(".") or "unknown"
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
    summary = {
        "matchedBranchCount": len(roots),
        "scannedFolderCount": scanned_folders,
        "coordinateSourceCount": len(found),
        "sourceCountByType": dict(sorted(suffix_counts.items())),
        "dateFrom": dated[0] if dated else None,
        "dateTo": dated[-1] if dated else None,
    }
    return found, summary


def folder_name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


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
            if folder_name_key(item.get("name", "")) in RAW_DATA_FOLDER_KEYS:
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


def download_file(service, item: dict, target: Path) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    target.parent.mkdir(parents=True, exist_ok=True)
    if item.get("mimeType") == SHEET_MIME:
        request = service.files().export_media(fileId=item["id"], mimeType=XLSX_MIME)
    else:
        request = service.files().get_media(fileId=item["id"], supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request, chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    target.write_bytes(buffer.getvalue())


def run(command: list[str], cwd: Path) -> None:
    print("Running:", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def sync(config_path: Path, repo: Path, workspace: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    projects = config.get("projects", [])
    required_keys = {"hetsuu-hutul", "artsat", "buduunkhad"}
    if {item.get("key") for item in projects} != required_keys:
        raise ValueError("drive-sync-sources.json must configure exactly the three project trackers")
    if workspace.exists():
        shutil.rmtree(workspace)
    workbooks_dir = workspace / "workbooks"
    sources_dir = workspace / "sources"
    reports_dir = workspace / "reports"
    workbooks_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    service = build_drive_service()
    raw_root_folder_id = os.environ.get("NERGUI_UNDUR_RAW_ROOT_FOLDER_ID", "").strip()
    hetsuu_root_folder_id = os.environ.get("HETSUU_HUTUL_ROOT_FOLDER_ID", "").strip()
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
    hetsuu_root_sources = []
    hetsuu_drone_summary = None
    if hetsuu_root_folder_id:
        try:
            hetsuu_root_sources, hetsuu_drone_summary = find_drone_sources(
                service,
                hetsuu_root_folder_id,
                int(config.get("projectRootDiscoveryDepth", 5)),
                int(config.get("projectRootScanDepth", 8)),
            )
            print("Hetsuu Hutul project-root drone source discovery:")
            print(json.dumps(hetsuu_drone_summary, ensure_ascii=False, indent=2))
        except Exception as error:
            print(f"::warning::Hetsuu Hutul project-root drone source discovery skipped: {error}")
    else:
        print("::notice::HETSUU_HUTUL_ROOT_FOLDER_ID is not configured; project-root drone discovery skipped.")
    max_depth = int(config.get("folderScanDepth", 2))
    max_size = int(config.get("maxSourceSizeBytes", 100 * 1024 * 1024))
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
        seen_folders = {}
        used_source_ids: set[str] = set()
        mismatched = []
        for row in rows:
            candidates = seen_folders.get(row["folderId"])
            if candidates is None:
                candidates = find_sources(service, row["folderId"], max_depth)
                seen_folders[row["folderId"]] = candidates
            chosen = choose_source(candidates, row["date"], row["mission"], max_size, used_source_ids)
            if not chosen:
                wrong_dates = sorted({source_date(item.get("name", "")) for item in candidates if source_date(item.get("name", ""))})
                if wrong_dates:
                    mismatched.append({"trackerId": row["trackerId"], "date": row["date"], "sourceDates": wrong_dates})
                continue
            used_source_ids.add(chosen["id"])
            local_name = f"{row['trackerId'].split(':')[-1].zfill(4)}__{chosen['id']}__{safe_filename(chosen['name'])}"
            local_path = project_source_dir / local_name
            download_file(service, chosen, local_path)
            mapping_entries.append({
                "file": local_name,
                "trackerId": row["trackerId"],
                "mission": row["mission"],
                "sourceUrl": f"https://drive.google.com/file/d/{chosen['id']}/view",
            })
        if project["key"] == "hetsuu-hutul" and hetsuu_root_sources:
            tracker_dates = {row["date"] for row in rows if row.get("date")}
            tracker_missions = {
                comparable_name(row["mission"]): row
                for row in rows
                if comparable_name(row.get("mission", ""))
            }
            for candidate in hetsuu_root_sources:
                if candidate.get("id") in used_source_ids:
                    continue
                candidate_key = comparable_name(candidate.get("relativePath", candidate.get("name", "")))
                exact_rows = [row for key, row in tracker_missions.items() if key in candidate_key]
                candidate_date = candidate.get("sourceDate")
                if len(exact_rows) != 1 and candidate_date not in tracker_dates:
                    # The current Hetsuu root also contains verified 2025 UAV
                    # magnetic survey lines. Keep them in the inventory log,
                    # but never attach them to an unrelated 2026 tracker row.
                    continue
                try:
                    size = int(candidate.get("size") or 0)
                except (TypeError, ValueError):
                    size = 0
                if size > max_size:
                    continue
                used_source_ids.add(candidate["id"])
                local_name = f"root__{candidate['id']}__{safe_filename(candidate['name'])}"
                local_path = project_source_dir / local_name
                download_file(service, candidate, local_path)
                entry = {
                    "file": local_name,
                    "sourceUrl": f"https://drive.google.com/file/d/{candidate['id']}/view",
                }
                if len(exact_rows) == 1:
                    entry.update({
                        "trackerId": exact_rows[0]["trackerId"],
                        "mission": exact_rows[0]["mission"],
                    })
                mapping_entries.append(entry)
        mapping_path = workspace / f"{project['key']}-mapping.json"
        mapping_path.write_text(json.dumps({"sources": mapping_entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        project_results[project["key"]] = {
            "trackerRowsWithFolders": len(rows),
            "coordinateSources": len(mapping_entries),
            "dateMismatches": mismatched,
            "sourceDir": project_source_dir,
            "mapping": mapping_path,
        }
        if project["key"] == "hetsuu-hutul" and hetsuu_drone_summary is not None:
            project_results[project["key"]]["projectRootDroneDiscovery"] = hetsuu_drone_summary

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
        result["importReport"] = json.loads(report.read_text(encoding="utf-8"))
        result.pop("sourceDir")
        result.pop("mapping")

    run([
        python, str(repo / "tools" / "export_project_flight_coverage.py"),
        str(tracks),
        "--licences", str(context_dir / "licenses.geojson"),
        "--output", str(context_dir / "project-flight-coverage.json"),
    ], repo)
    summary = {
        "projects": project_results,
        "nerguiUndurRawSensorCounts": raw_sensor_summary,
        "hetsuuHutulDroneSources": hetsuu_drone_summary,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    sync(args.config.resolve(), args.repo.resolve(), args.workspace.resolve())


if __name__ == "__main__":
    main()
