"""Validate committed Nergui Undur registry and GeoJSON truth metadata."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "dist"
DATA = ROOT / "data"
FORBIDDEN = ("artsat", "buduunkhad", "buduun khad")
VALID_GEOMETRY = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_geojson(path: Path, expected_project: str = "Nergui Undur") -> list[str]:
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
        if project != expected_project:
            errors.append(f"{path}:{fid}: project must be {expected_project!r}, got {project!r}")
        text = json.dumps(props, ensure_ascii=False).lower()
        if any(token in text for token in FORBIDDEN):
            errors.append(f"{path}:{fid}: another project name is present")
        if props.get("dataType") == "actual_flight_track":
            source_file = str(props.get("sourceFile", "")).lower()
            if re.search(r"\.(kmz|kml|wpmz|zip)$", source_file) or props.get("plannedActual") != "actual":
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
        if dataset.get("project") != "Nergui Undur":
            errors.append(f"datasets.json:{dataset_id}: wrong project")
        asset = dataset.get("webAsset")
        if asset:
            path = ROOT / asset.removeprefix("./")
            if not path.exists():
                errors.append(f"datasets.json:{dataset_id}: missing {asset}")
            elif path.suffix == ".geojson":
                errors.extend(validate_geojson(path))
    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Validated {len(dataset_ids)} datasets; no duplicate IDs, cross-project markers, or planned/actual conflicts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
