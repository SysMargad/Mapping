"""Apply truthful Nergui Undur metadata to the committed web assets.

This is an idempotent migration for the pre-registry project data.  Geometry is
read and written unchanged; only feature properties, IDs, layer summaries, and
collection metadata are normalised.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "dist" / "data"
FORBIDDEN_PROJECTS = ("artsat", "buduunkhad", "buduun khad")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def assert_nergui_source(payload: dict, path: Path) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    found = [name for name in FORBIDDEN_PROJECTS if name in text]
    if found:
        raise ValueError(f"{path}: cross-project marker(s) found: {', '.join(found)}")


def normalise_magarrow_plan() -> None:
    path = ROOT / "magarrow" / "planned-survey.geojson"
    data = load(path)
    assert_nergui_source(data, path)
    rename = {
        "P1_Main_50m": "MagArrow_Main_50m",
        "P1_Main_100m_AZ88": "MagArrow_Main_100m_AZ88",
        "P1_Tie_300m": "MagArrow_Tie_300m",
        "P1_Tie_200m_AZ178": "MagArrow_Tie_200m_AZ178",
    }
    for feature in data.get("features", []):
        props = feature.setdefault("properties", {})
        old_layer = str(props.get("layer", ""))
        layer = rename.get(old_layer, old_layer)
        props["layer"] = layer
        props.update({
            "project": "Nergui Undur",
            "area": "Heseg Uul hoid",
            "sensor": "MagArrow",
            "plannedActual": "planned",
            "status": "confirmed",
            "source_epsg": 32649,
            "display_epsg": 4326,
            "crs_verified": True,
        })
        if layer.startswith("MagArrow_Main"):
            props["dataType"] = "planned_main_line"
        elif layer.startswith("MagArrow_Tie"):
            props["dataType"] = "planned_tie_line"
        elif layer == "Survey_Area":
            props["dataType"] = "planned_survey_boundary"
        elif layer == "Track_Start_End":
            props["dataType"] = "planned_waypoint"
        else:
            props["dataType"] = "planned_metadata"
        if isinstance(feature.get("id"), str):
            feature["id"] = feature["id"].replace(old_layer, layer, 1)
    for summary in data.get("layers", []):
        summary["name"] = rename.get(summary.get("name"), summary.get("name"))
        summary["display_epsg"] = 4326
        summary["crs_verified"] = summary.get("source_epsg") == 32649
    data.update({
        "name": "Nergui Undur MagArrow Planned Survey",
        "project": "Nergui Undur",
        "area": "Heseg Uul hoid",
        "sensor": "MagArrow",
        "dataType": "planned_survey",
        "plannedActual": "planned",
    })
    save(path, data)


def normalise_missions() -> None:
    path = ROOT / "magarrow" / "mission-plans.geojson"
    data = load(path)
    assert_nergui_source(data, path)
    for feature in data.get("features", []):
        props = feature.setdefault("properties", {})
        mission = str(props.get("plan") or props.get("mission_id") or "")
        props.update({
            "layer": "MagArrow_Mission",
            "project": "Nergui Undur",
            "area": "Heseg Uul hoid",
            "sensor": "MagArrow",
            "dataType": "mission_plan",
            "plannedActual": "planned",
            "status": "confirmed",
            "mission_id": mission,
            "sourceFile": f"{mission}.zip",
            "sourceUrl": "https://drive.google.com/drive/folders/1n5C1QrH1ZQfuFOOpDfpGk_4hgrwBdlXS",
            "sourceCrs": "EPSG:4326",
            "displayCrs": "EPSG:4326",
            "crsVerified": True,
        })
    for plan in data.get("plans", []):
        mission = str(plan.get("label", ""))
        plan["mission_id"] = mission
        plan["sourceFile"] = f"{mission}.zip"
    data.update({
        "name": "MagArrow DJI Mission Plans",
        "project": "Nergui Undur",
        "area": "Heseg Uul hoid",
        "sensor": "MagArrow",
        "dataType": "mission_plan",
        "plannedActual": "planned",
    })
    save(path, data)


def boundary_layer(old: str) -> tuple[str, str]:
    existing = {
        "Base_Block_Boundary": "block_boundary",
        "Base_Block_Label": "block_label",
        "Base_Uchastik_Boundary": "uchastik_boundary",
        "Base_Uchastik_Label": "uchastik_label",
        "Base_CAD_Reference": "cad_reference_geometry",
    }
    if old in existing:
        return old, existing[old]
    lower = old.lower()
    if old == "BLOCK_BOUNDARY":
        return "Base_Block_Boundary", "block_boundary"
    if old == "BLOCK_NUM_LABELS":
        return "Base_Block_Label", "block_label"
    if lower == "uchastic":
        return "Base_Uchastik_Boundary", "uchastik_boundary"
    if lower == "uchastic_labels":
        return "Base_Uchastik_Label", "uchastik_label"
    return "Base_CAD_Reference", "cad_reference_geometry"


def normalise_boundaries() -> None:
    path = ROOT / "base" / "survey-boundaries.geojson"
    data = load(path)
    assert_nergui_source(data, path)
    counts: dict[str, dict] = {}
    for feature in data.get("features", []):
        props = feature.setdefault("properties", {})
        old = str(props.get("layer", ""))
        cad_layer = str(props.get("cad_layer", ""))
        if cad_layer.lower() == "uchastic":
            old = "uchastic" if feature.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"} else "uchastic_LABELS"
        elif old == "Base_CAD_Reference":
            if props.get("block_num"):
                old = "BLOCK_BOUNDARY"
            elif cad_layer == "BLOCK_NUM":
                old = "BLOCK_NUM_LABELS"
        layer, data_type = boundary_layer(old)
        props.pop("source_epsg", None)
        props.update({
            "layer": layer,
            "project": "Nergui Undur",
            "area": "Nergui Undur",
            "sensor": "Base / Control",
            "dataType": data_type,
            "plannedActual": "reference",
            "status": "confirmed_geometry",
            "sourceCrs": "UNKNOWN",
            "source_crs_verified": False,
            "displayCrs": "EPSG:4326",
            "crsVerified": False,
            "displayGeometryStatus": "converted_or_assigned_for_web_review",
        })
        summary = counts.setdefault(layer, {
            "name": layer,
            "geometry_type": feature.get("geometry", {}).get("type", "UNKNOWN").upper(),
            "sourceCrs": "UNKNOWN",
            "source_crs_verified": False,
            "displayCrs": "EPSG:4326",
            "feature_count": 0,
        })
        summary["feature_count"] += 1
        feature["id"] = f"{layer}:{summary['feature_count']}"
    data.update({
        "name": "Nergui Undur Base / Control Geometry",
        "project": "Nergui Undur",
        "sensor": "Base / Control",
        "dataType": "base_control",
        "sourceCrs": "UNKNOWN",
        "source_crs_verified": False,
        "displayCrs": "EPSG:4326",
        "layers": list(counts.values()),
    })
    save(path, data)


def normalise_base_file(filename: str, layer: str, data_type: str, source_crs: str) -> None:
    path = ROOT / "base" / filename
    data = load(path)
    if data_type == "licence_boundary":
        data["features"] = [
            feature for feature in data.get("features", [])
            if str(feature.get("properties", {}).get("AREANAME", "")).strip().lower() == "nergui undur"
            or "нэргүй" in str(feature.get("properties", {}).get("AREANAME_L", "")).strip().lower()
        ]
        if not data["features"]:
            raise ValueError(f"{path}: Nergui Undur licence feature was not found")
    assert_nergui_source(data, path)
    prefix = "licence" if data_type == "licence_boundary" else "uchastik"
    for index, feature in enumerate(data.get("features", []), start=1):
        props = feature.setdefault("properties", {})
        props.update({
            "layer": layer,
            "project": "Nergui Undur",
            "sensor": "Base / Control",
            "dataType": data_type,
            "plannedActual": "reference",
            "status": "confirmed",
            "sourceCrs": source_crs,
            "displayCrs": "EPSG:4326",
            "crsVerified": True,
        })
        feature["id"] = f"{prefix}:{index}"
    data["project"] = "Nergui Undur"
    data["dataType"] = data_type
    save(path, data)


if __name__ == "__main__":
    normalise_magarrow_plan()
    normalise_missions()
    normalise_boundaries()
    normalise_base_file("licenses.geojson", "Base_Licence_Boundary", "licence_boundary", "EPSG:4326")
    normalise_base_file("uchastics.geojson", "Base_Uchastik_Boundary", "uchastik_boundary", "EPSG:32649")
    print("Normalised committed Nergui Undur assets without changing geometry.")
