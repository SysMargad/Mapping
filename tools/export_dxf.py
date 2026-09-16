"""Convert an ASCII DXF boundary/line drawing to WGS84 GeoJSON.

The exporter supports LINE, LWPOLYLINE and POLYLINE entities. Coordinates are
assumed to be UTM Zone 49N unless another zone is supplied.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from export_gpkg import geometry_area, utm_to_wgs84


def pairs(lines: list[str]) -> list[tuple[int, str]]:
    if len(lines) % 2:
        lines = lines[:-1]
    return [(int(lines[index].strip()), lines[index + 1].strip()) for index in range(0, len(lines), 2)]


def entities(path: Path) -> list[dict]:
    values = pairs(path.read_text(encoding="cp1252", errors="replace").splitlines())
    result = []
    index = 0
    while index < len(values):
        if values[index] != (0, "SECTION"):
            index += 1
            continue
        index += 1
        if index >= len(values) or values[index] != (2, "ENTITIES"):
            continue
        index += 1
        while index < len(values) and values[index] != (0, "ENDSEC"):
            if values[index][0] != 0:
                index += 1
                continue
            kind = values[index][1]
            start = index + 1
            index = start
            while index < len(values) and values[index][0] != 0:
                index += 1
            data = values[start:index]
            if kind == "LINE":
                coords = {code: float(value) for code, value in data if code in (10, 20, 11, 21)}
                if all(code in coords for code in (10, 20, 11, 21)):
                    result.append({"type": "LineString", "points": [[coords[10], coords[20]], [coords[11], coords[21]]]})
            elif kind == "LWPOLYLINE":
                points = []
                closed = False
                current = None
                for code, value in data:
                    if code == 10:
                        current = [float(value), None]
                        points.append(current)
                    elif code == 20 and current is not None:
                        current[1] = float(value)
                    elif code == 70:
                        closed = bool(int(value) & 1)
                points = [point for point in points if point[1] is not None]
                if len(points) >= 2:
                    if closed and points[0] != points[-1]:
                        points.append(points[0])
                    result.append({"type": "Polygon" if closed else "LineString", "points": points})
        index += 1
        break
    return result


def export(source: Path, output: Path, zone: int) -> None:
    features = []
    for index, entity in enumerate(entities(source), start=1):
        coordinates = [utm_to_wgs84(point[0], point[1], zone, True) for point in entity["points"]]
        geometry = {"type": entity["type"], "coordinates": [coordinates] if entity["type"] == "Polygon" else coordinates}
        properties = {"layer": "L3", "entity": entity["type"], "source_epsg": 32600 + zone}
        area = geometry_area(geometry)
        if area is not None:
            properties["area_m2"] = round(area, 2)
        features.append({"type": "Feature", "id": f"L3:{index}", "properties": properties, "geometry": geometry})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "type": "FeatureCollection", "name": source.stem, "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Exported {len(features)} L3 feature(s) to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--utm-zone", type=int, default=49)
    args = parser.parse_args()
    export(args.source, args.output, args.utm_zone)
