"""Import verified flight trajectories from Google Drive for desktop.

This is the workstation equivalent of the Drive API sync.  It walks the four
configured project roots exposed by Google Drive for desktop, but reads only
small coordinate sidecars that can be matched to an existing tracker mission.
Image payloads are never copied or opened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coordinate_sources as cs
import import_project_flight_tracks as flight_importer


PROJECT_KEYS = tuple(flight_importer.PROJECTS)
SKIP_DIRECTORY_NAMES = frozenset({
    ".stfolder", "exiftool_files", "__pycache__", "$recycle.bin",
})


def normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def load_project_roots(config_path: Path, drive_root: Path) -> dict[str, Path]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    roots = {}
    for project in config.get("projects", []):
        key = project.get("key")
        folder_id = project.get("rootFolderId")
        if key in PROJECT_KEYS and folder_id:
            roots[key] = drive_root / folder_id
    missing = sorted(set(PROJECT_KEYS) - set(roots))
    if missing:
        raise ValueError(f"Missing local project root configuration: {', '.join(missing)}")
    return roots


def tracker_missions(trackers_path: Path, project_key: str) -> dict[str, dict]:
    data = json.loads(trackers_path.read_text(encoding="utf-8"))
    result: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for record in data.get("records", []):
        if record.get("projectKey") != project_key:
            continue
        key = normalise(record.get("mission", ""))
        if not key:
            continue
        if key in result:
            ambiguous.add(key)
        else:
            result[key] = record
    for key in ambiguous:
        result.pop(key, None)
    return result


def candidate_priority(path: Path) -> int | None:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name == "metadata.csv":
        return 0
    if name.endswith("_timestamp.mrk"):
        return 1
    if name.startswith("djiflightrecord") and suffix in {".kmz", ".kml"}:
        return 2
    if suffix in {".csv", ".txt"}:
        return 3
    return None


def coordinate_table(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            sample = handle.read(65536)
        return cs.header_has_coordinates(sample)
    except OSError:
        return False


def match_mission(relative_path: str, missions: dict[str, dict]) -> tuple[str, dict] | None:
    key = normalise(relative_path)
    matches = [(mission, record) for mission, record in missions.items() if mission in key]
    if not matches:
        return None
    # A folder can contain a shortened mission prefix and its full child name.
    # The longest exact tracker token is the most specific, deterministic match.
    matches.sort(key=lambda item: len(item[0]), reverse=True)
    if len(matches) > 1 and len(matches[0][0]) == len(matches[1][0]):
        return None
    return matches[0]


def discover_sources(
    root: Path,
    missions: dict[str, dict],
    all_missions: dict[str, dict] | None = None,
) -> tuple[dict[str, Path], dict]:
    all_missions = all_missions or missions
    selected: dict[str, tuple[int, int, Path]] = {}
    stats = {
        "rootReadable": root.is_dir(),
        "directoriesScanned": 0,
        "candidateFiles": 0,
        "matchedMissions": 0,
        "readErrors": 0,
    }
    if not stats["rootReadable"]:
        return {}, stats

    def consider(path: Path, relative: str, known_match=None) -> None:
        priority = candidate_priority(path)
        if priority is None:
            return
        stats["candidateFiles"] += 1
        match = known_match or match_mission(relative, missions)
        if not match:
            return
        mission_key, _record = match
        if path.suffix.lower() in {".csv", ".txt"} and not coordinate_table(path):
            return
        deleted_penalty = 1 if "delete" in relative.lower() else 0
        rank = (deleted_penalty, priority)
        current_choice = selected.get(mission_key)
        if current_choice is None or rank < current_choice[:2]:
            selected[mission_key] = (rank[0], rank[1], path)

    stack = [root]
    while stack:
        current_path = stack.pop()
        stats["directoriesScanned"] += 1
        relative_dir = str(current_path.relative_to(root)) if current_path != root else ""
        any_directory_match = match_mission(relative_dir, all_missions) if relative_dir else None
        if any_directory_match:
            mission_key, record = any_directory_match
            directory_match = (mission_key, record) if mission_key in missions else None
            if directory_match is None:
                # This mission already has a published trackerId. Do not enter
                # its photo directory during an incremental daily scan.
                continue
            # Mission folders can contain tens of thousands of photos. Probe
            # the two deterministic coordinate-sidecar names directly and do
            # not enumerate the photo names.
            probes = [
                current_path / "metadata.csv",
                current_path / f"{record['mission']}_Timestamp.MRK",
            ]
            if normalise(current_path.name) != normalise(record["mission"]):
                probes.append(current_path / f"{current_path.name}_Timestamp.MRK")
            for path in probes:
                try:
                    if path.is_file():
                        consider(path, str(path.relative_to(root)), directory_match)
                        # A coordinate-bearing metadata.csv is the preferred
                        # source, so avoid two more remote filesystem probes.
                        if selected.get(mission_key, (9, 9, None))[1] == 0:
                            break
                except OSError:
                    stats["readErrors"] += 1
            continue
        try:
            with os.scandir(current_path) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() not in SKIP_DIRECTORY_NAMES:
                                stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            path = Path(entry.path)
                            if candidate_priority(path) is not None:
                                consider(path, str(path.relative_to(root)))
                    except OSError:
                        stats["readErrors"] += 1
        except OSError:
            stats["readErrors"] += 1
    stats["matchedMissions"] = len(selected)
    return {key: value[2] for key, value in selected.items()}, stats


def stage_sources(sources: dict[str, Path], missions: dict[str, dict], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for mission_key, source in sources.items():
        record = missions[mission_key]
        mission = record["mission"]
        suffix = source.suffix.lower()
        digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:10]
        if source.name.lower() == "metadata.csv":
            filename = f"{mission}__metadata_{digest}.csv"
        else:
            filename = f"{source.stem}_{digest}{suffix}"
        shutil.copy2(source, target / filename)


def sync_project(
    project_key: str,
    root: Path,
    trackers: Path,
    licences: Path,
    output: Path,
    staging_parent: Path,
    report_dir: Path | None,
    full_refresh: bool = False,
    since: str | None = None,
) -> dict:
    all_missions = tracker_missions(trackers, project_key)
    existing_data = json.loads(output.read_text(encoding="utf-8"))
    published_tracker_ids = {
        feature.get("properties", {}).get("trackerId")
        for feature in existing_data.get("features", [])
        if feature.get("properties", {}).get("projectKey") == project_key
    }
    missions = {
        key: record for key, record in all_missions.items()
        if (full_refresh or record.get("id") not in published_tracker_ids)
        and (not since or str(record.get("date", "")) >= since)
    }
    if not root.is_dir():
        raise FileNotFoundError(f"Google Drive project root is unavailable: {project_key}: {root}")
    if not missions:
        return {
            "project": project_key,
            "rootReadable": True,
            "directoriesScanned": 0,
            "candidateFiles": 0,
            "matchedMissions": 0,
            "readErrors": 0,
            "trackerRecords": len(all_missions),
            "pendingTrackerRecords": 0,
            "imported": 0,
            "unmatchedCount": 0,
            "rejectedCount": 0,
            "reasonCounts": {},
        }
    sources, scan = discover_sources(root, missions, all_missions)
    if not scan["rootReadable"]:
        raise FileNotFoundError(f"Google Drive project root is unavailable: {project_key}: {root}")
    source_dir = staging_parent / project_key
    stage_sources(sources, missions, source_dir)
    report_path = report_dir / f"local-sync-{project_key}.json" if report_dir else None
    args = SimpleNamespace(
        source_dir=source_dir,
        project=project_key,
        trackers=trackers,
        licences=licences,
        existing=output,
        output=output,
        mapping=None,
        report=report_path,
    )
    report = flight_importer.build(args)
    return {
        "project": project_key,
        **scan,
        "trackerRecords": len(all_missions),
        "pendingTrackerRecords": len(missions),
        "imported": report["imported"],
        "unmatchedCount": len(report["unmatched"]),
        "rejectedCount": len(report["rejected"]),
        "reasonCounts": report["reasonCounts"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--drive-root", type=Path, default=Path(r"G:\.shortcut-targets-by-id"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--trackers", type=Path)
    parser.add_argument("--licences", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--full-refresh", action="store_true")
    parser.add_argument("--since", help="Only consider tracker records on or after YYYY-MM-DD")
    parser.add_argument("--projects", nargs="+", choices=PROJECT_KEYS, default=list(PROJECT_KEYS))
    args = parser.parse_args()

    repo = args.repo.resolve()
    config = args.config or repo / "drive-sync-sources.json"
    trackers = args.trackers or repo / "dist/data/context/project-trackers.json"
    licences = args.licences or repo / "dist/data/context/licenses.geojson"
    output = args.output or repo / "dist/data/context/project-flight-tracks.geojson"
    roots = load_project_roots(config, args.drive_root)
    if args.report_dir:
        args.report_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mapping-local-sync-") as temporary:
        temporary_path = Path(temporary)
        effective_output = output
        if args.dry_run:
            effective_output = temporary_path / output.name
            shutil.copy2(output, effective_output)
        summaries = []
        for project_key in args.projects:
            summary = sync_project(
                project_key,
                roots[project_key],
                trackers,
                licences,
                effective_output,
                temporary_path / "sources",
                args.report_dir,
                args.full_refresh,
                args.since,
            )
            summaries.append(summary)
            print(json.dumps({
                "project": project_key,
                "matchedMissions": summary["matchedMissions"],
                "imported": summary["imported"],
                "rejectedCount": summary["rejectedCount"],
            }, ensure_ascii=False), flush=True)

    result = {
        "dryRun": args.dry_run,
        "projectCount": len(summaries),
        "importedTotal": sum(item["imported"] for item in summaries),
        "projects": summaries,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
