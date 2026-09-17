"""Convert the Hetsuu hutul binary DWG to browser-ready GeoJSON.

LibreDWG's ``dwgread`` performs the binary DWG decoding. Coordinates in the
source drawing are WGS 84 / UTM zone 46N and are transformed to EPSG:4326 by
the dependency-free projection helper already used by the GeoPackage exporter.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from export_gpkg import geometry_area, utm_to_wgs84


VALID_TYPES = {"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"}
SOURCE_URL = "https://drive.google.com/drive/folders/1hopqTCeMJknMkkvMa_wCY6OQ_GHXt4ub"


def projected_points(value):
    if not isinstance(value, list):
        return
    if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        yield value
        return
    for item in value:
        yield from projected_points(item)


def valid_utm_geometry(geometry: dict) -> bool:
    points = list(projected_points(geometry.get("coordinates")))
    return bool(points) and all(
        math.isfinite(point[0])
        and math.isfinite(point[1])
        and 100000 <= point[0] <= 900000
        and 0 <= point[1] <= 10000000
        for point in points
    )


def transform_coordinates(value, zone: int):
    if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        return utm_to_wgs84(float(value[0]), float(value[1]), zone, True)
    return [transform_coordinates(item, zone) for item in value]


def display_role(geometry_type: str, layer: str, text: str) -> str:
    if geometry_type in {"Point", "MultiPoint"}:
        if text and any(character.isalpha() for character in text):
            return "label"
        return "elevation" if text else "control_point"
    if geometry_type in {"LineString", "MultiLineString"}:
        return "line"
    if "BOUNDARY" in layer or "HIL" in layer or layer == "SITE_BOUNDARY":
        return "boundary"
    if layer == "BLOCKS":
        return "block"
    if layer == "HESEG" or layer.startswith("HESEG_"):
        return "section"
    return "polygon"


def export(source: Path, output: Path, dwgread: Path, zone: int) -> None:
    if not source.exists():
        raise FileNotFoundError(f"DWG source not found: {source}")
    if not dwgread.exists():
        raise FileNotFoundError(
            f"LibreDWG dwgread not found: {dwgread}. Set LIBREDWG_DWGREAD or install the portable tool."
        )

    with tempfile.TemporaryDirectory(prefix="mapping-dwg-") as temp_dir:
        raw_path = Path(temp_dir) / "raw.geojson"
        result = subprocess.run(
            [str(dwgread), "-O", "GeoJSON", "-o", str(raw_path), str(source)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not raw_path.exists():
            detail = (result.stderr or result.stdout or "unknown converter error").strip()
            raise RuntimeError(f"LibreDWG conversion failed: {detail}")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))

    features = []
    dropped = 0
    layer_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    for index, raw_feature in enumerate(raw.get("features", []), start=1):
        geometry = raw_feature.get("geometry") or {}
        geometry_type = geometry.get("type")
        if geometry_type not in VALID_TYPES or not valid_utm_geometry(geometry):
            dropped += 1
            continue
        raw_props = raw_feature.get("properties") or {}
        layer = str(raw_props.get("Layer") or "0")
        text = str(raw_props.get("Text") or "").strip()
        role = display_role(geometry_type, layer, text)
        transformed = {
            "type": geometry_type,
            "coordinates": transform_coordinates(geometry["coordinates"], zone),
        }
        area = geometry_area(transformed) if geometry_type in {"Polygon", "MultiPolygon"} else None
        properties = {
            "project": "External licence reference",
            "scope": "external_reference",
            "area": "Hetsuu hutul",
            "sensor": "Base / Control",
            "dataType": "cad_reference_geometry",
            "displayRole": role,
            "plannedActual": "reference",
            "status": "confirmed_geometry",
            "contextOnly": True,
            "sourceFile": source.name,
            "sourceUrl": SOURCE_URL,
            "sourceCrs": f"EPSG:326{zone:02d}",
            "displayCrs": "EPSG:4326",
            "crsVerified": True,
            "cadLayer": layer,
            "entityClass": raw_props.get("SubClasses"),
            "entityHandle": raw_props.get("EntityHandle"),
            "cadColor": raw_props.get("Color"),
            "text_value": text or None,
            "area_m2": round(area, 2) if area is not None else None,
        }
        properties = {key: value for key, value in properties.items() if value is not None}
        features.append({
            "type": "Feature",
            "id": f"hetsuu-dwg:{index}",
            "properties": properties,
            "geometry": transformed,
        })
        layer_counts[layer] += 1
        role_counts[role] += 1

    if not features:
        raise RuntimeError("DWG conversion produced no located geometry")

    payload = {
        "type": "FeatureCollection",
        "project": "External licence reference",
        "scope": "external_reference",
        "area": "Hetsuu hutul",
        "sourceFile": source.name,
        "sourceUrl": SOURCE_URL,
        "sourceCrs": f"EPSG:326{zone:02d}",
        "displayCrs": "EPSG:4326",
        "crsVerified": True,
        "featureCount": len(features),
        "droppedUnlocatedCount": dropped,
        "layerCounts": dict(sorted(layer_counts.items())),
        "roleCounts": dict(sorted(role_counts.items())),
        "features": features,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Exported {len(features)} located DWG features; dropped {dropped} unlocated/unsupported features -> {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dwgread", required=True, type=Path)
    parser.add_argument("--utm-zone", type=int, default=46)
    args = parser.parse_args()
    export(args.source, args.output, args.dwgread, args.utm_zone)


if __name__ == "__main__":
    main()
