"""Convert DJI KMZ/WPMZ mission-plan lines into a browser GeoJSON layer.

The source geometry is a planned route stored in ``wpmz/template.kml``.  It is
deliberately published as ``planned`` and must never be presented as an actual
flown trajectory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


DATE_RE = re.compile(r"(20\d{2})[-.](\d{1,2})[-.](\d{1,2})")


def route_date(path: Path) -> str | None:
    for part in reversed(path.parts):
        match = DATE_RE.search(part)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return None


def parse_coordinates(text: str | None) -> list[list[float]]:
    points: list[list[float]] = []
    for token in (text or "").split():
        values = token.split(",")
        try:
            lon, lat = float(values[0]), float(values[1])
        except (IndexError, ValueError):
            continue
        if not (112 <= lon <= 115 and 47 <= lat <= 51):
            continue
        point = [round(lon, 9), round(lat, 9)]
        if not points or point != points[-1]:
            points.append(point)
    return points


def parse_kmz(path: Path) -> list[list[list[float]]]:
    with zipfile.ZipFile(path) as archive:
        kml_names = [name for name in archive.namelist() if name.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError("KMZ contains no KML document")
        root = ET.fromstring(archive.read(kml_names[0]))
    lines = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "coordinates":
            points = parse_coordinates(element.text)
            if len(points) >= 2:
                lines.append(points)
    return lines


def length_metres(points: list[list[float]]) -> float:
    radius = 6_371_008.8
    total = 0.0
    for first, second in zip(points, points[1:]):
        lat1, lat2 = math.radians(first[1]), math.radians(second[1])
        dlat = lat2 - lat1
        dlon = math.radians(second[0] - first[0])
        value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        total += 2 * radius * math.asin(math.sqrt(value))
    return round(total, 2)


def feature_id(day: str | None, path: Path, segment: int) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
    if not stem:
        stem = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:10]
    return f"l3-plan-{day or 'undated'}-{stem}-{segment:02d}"


def build(source: Path) -> dict:
    features = []
    for path in sorted(source.rglob("*.kmz"), key=lambda item: str(item).lower()):
        day = route_date(path.relative_to(source))
        for segment, coordinates in enumerate(parse_kmz(path), 1):
            name = path.stem
            line_type = "tie_line" if "tieline" in name.lower() else (
                "survey_line" if "sant" in name.lower() else "planned_line"
            )
            features.append({
                "type": "Feature",
                "id": feature_id(day, path, segment),
                "properties": {
                    "project": "Nergui Undur",
                    "area": "Sant Uul",
                    "sensor": "L3",
                    "dataType": "mission_plan_line",
                    "plannedActual": "planned",
                    "status": "confirmed_source",
                    "date": day,
                    "plan": name,
                    "lineType": line_type,
                    "sourceFile": path.name,
                    "sourceKind": "DJI WPMZ/KMZ mission plan route",
                    "displayCrs": "EPSG:4326",
                    "crsVerified": True,
                    "pointCount": len(coordinates),
                    "lengthM": length_metres(coordinates),
                    "contextOnly": False,
                },
                "geometry": {"type": "LineString", "coordinates": coordinates},
            })
    features.sort(key=lambda feature: (
        feature["properties"].get("date") or "",
        feature["properties"]["plan"],
        feature["id"],
    ))
    return {
        "type": "FeatureCollection",
        "name": "Nergui Undur L3 planned survey lines",
        "project": "Nergui Undur",
        "dataType": "mission_plan_line",
        "featureCount": len(features),
        "sourceFolder": "Google Drive planned-line archive",
        "features": features,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = build(args.source)
    if not payload["features"]:
        raise SystemExit("No valid KMZ mission-plan lines found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(list(args.source.rglob('*.kmz'))), "features": payload["featureCount"]}))


if __name__ == "__main__":
    main()
