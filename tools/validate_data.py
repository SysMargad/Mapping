"""Validate committed Nergui Undur registry and GeoJSON truth metadata."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "dist"
DATA = ROOT / "data"
FORBIDDEN = ("artsat", "buduunkhad", "buduun khad")
PROJECT_OPERATION_NAMES = {"Hetsuu hutul", "Artsat", "Buduunkhad"}
VALID_GEOMETRY = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_geojson(
    path: Path,
    expected_project: str = "Nergui Undur",
    external_context: bool = False,
    expected_data_type: str | None = None,
    project_operations: bool = False,
) -> list[str]:
    errors = []
    data = load(path)
    if data.get("type") != "FeatureCollection":
        errors.append(f"{path}: expected FeatureCollection")
    ids = set()
    for index, feature in enumerate(data.get("features", [])):
        fid = feature.get("id")
        if fid in ids:
            errors.append(f"{path}: duplicate feature id {fid}")
        ids.add(fid)
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in VALID_GEOMETRY:
            errors.append(f"{path}:{index}: unsupported geometry {geometry.get('type')}")
        props = feature.get("properties") or {}
        project = props.get("project") or data.get("project")
        if project_operations and project not in PROJECT_OPERATION_NAMES:
            errors.append(f"{path}:{fid}: unsupported project operations project {project!r}")
        elif not project_operations and project != expected_project:
            errors.append(f"{path}:{fid}: project must be {expected_project!r}, got {project!r}")
        text = json.dumps(props, ensure_ascii=False).lower()
        if not external_context and not project_operations and any(token in text for token in FORBIDDEN):
            errors.append(f"{path}:{fid}: another project name is present")
        if (external_context or project_operations) and props.get("contextOnly") is not True:
            errors.append(f"{path}:{fid}: external reference features must be context-only")
        if (external_context or project_operations) and expected_data_type and props.get("dataType") != expected_data_type:
            errors.append(
                f"{path}:{fid}: expected dataType {expected_data_type!r}, got {props.get('dataType')!r}"
            )
        if props.get("dataType") == "actual_flight_track":
            source_file = str(props.get("sourceFile", "")).lower()
            verified_flight_record = (
                props.get("flightRecordVerified") is True
                and source_file.startswith("djiflightrecord_")
                and source_file.endswith(".kmz")
            )
            if (re.search(r"\.(kmz|kml|wpmz|zip)$", source_file) and not verified_flight_record) or props.get("plannedActual") != "actual":
                errors.append(f"{path}:{fid}: mission plan cannot be an actual flight track")
            if geometry.get("type") not in ("LineString", "MultiLineString"):
                errors.append(f"{path}:{fid}: actual track must be a line")
    return errors


def main() -> int:
    errors = []
    registry = load(DATA / "datasets.json")
    dataset_ids = set()
    for dataset in registry.get("datasets", []):
        dataset_id = dataset.get("id")
        if dataset_id in dataset_ids:
            errors.append(f"datasets.json: duplicate id {dataset_id}")
        dataset_ids.add(dataset_id)
        external_context = dataset.get("scope") == "external_reference"
        project_operations = dataset.get("scope") == "project_operations"
        expected_project = (
            "External licence reference" if external_context
            else "Multi-project operations" if project_operations
            else "Nergui Undur"
        )
        if dataset.get("project") != expected_project:
            errors.append(f"datasets.json:{dataset_id}: wrong project")
        asset = dataset.get("webAsset")
        if asset:
            path = ROOT / asset.removeprefix("./")
            if not path.exists():
                errors.append(f"datasets.json:{dataset_id}: missing {asset}")
            elif path.suffix == ".geojson":
                errors.extend(validate_geojson(
                    path,
                    expected_project,
                    external_context,
                    dataset.get("dataType"),
                    project_operations,
                ))
            elif dataset_id == "context-project-trackers":
                payload = load(path)
                if payload.get("project") != "Multi-project operations" or payload.get("scope") != "project_operations":
                    errors.append(f"{path}: invalid project operations root metadata")
                if set(payload.get("projects", {})) != {"hetsuu-hutul", "artsat", "buduunkhad"}:
                    errors.append(f"{path}: expected exactly three tracker projects")
                if any(record.get("trajectoryAvailable") is not False for record in payload.get("records", [])):
                    errors.append(f"{path}: tracker records must not be classified as trajectories")
    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Validated {len(dataset_ids)} datasets; no duplicate IDs, unauthorised cross-project markers, or planned/actual conflicts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
