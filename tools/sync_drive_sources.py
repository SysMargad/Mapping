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
        mapping_path = workspace / f"{project['key']}-mapping.json"
        mapping_path.write_text(json.dumps({"sources": mapping_entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        project_results[project["key"]] = {
            "trackerRowsWithFolders": len(rows),
            "coordinateSources": len(mapping_entries),
            "dateMismatches": mismatched,
            "sourceDir": project_source_dir,
            "mapping": mapping_path,
        }

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
    summary = {"projects": project_results}
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
