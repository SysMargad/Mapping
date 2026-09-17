"""Build per-project, per-sensor daily coverage from verified flight tracks."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


CELL_SIZE = 10.0
SWATH_RADIUS = 25.0
METRES_PER_DEGREE = 111_320.0
PROJECT_LICENCES = {
    "hetsuu-hutul": "XV-022905",
    "artsat": "XV-021395",
    "buduunkhad": "XV-023222",
}


def local_project(point, lon0, lat0):
    return (
        (point[0] - lon0) * METRES_PER_DEGREE * math.cos(math.radians(lat0)),
        (point[1] - lat0) * METRES_PER_DEGREE,
    )


def point_segment_distance(point, start, end):
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_in_ring(point, ring):
    x, y = point
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def point_in_geometry(point, geometry):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry.get("coordinates", [])
    return any(
        polygon and point_in_ring(point, polygon[0])
        and not any(point_in_ring(point, hole) for hole in polygon[1:])
        for polygon in polygons
    )


def coverage_cells(feature, lon0, lat0):
    points = [local_project(point, lon0, lat0) for point in feature["geometry"]["coordinates"]]
    cells = set()
    radius_cells = math.ceil(SWATH_RADIUS / CELL_SIZE)
    for start, end in zip(points, points[1:]):
        distance = math.dist(start, end)
        if distance > 2_000:
            continue
        steps = max(1, math.ceil(distance / (CELL_SIZE / 2)))
        for step in range(steps + 1):
            t = step / steps
            x = start[0] + (end[0] - start[0]) * t
            y = start[1] + (end[1] - start[1]) * t
            centre_x, centre_y = math.floor(x / CELL_SIZE), math.floor(y / CELL_SIZE)
            for offset_x in range(-3, 4):
                for offset_y in range(-3, 4):
                    ix, iy = centre_x + offset_x, centre_y + offset_y
                    candidate = ((ix + 0.5) * CELL_SIZE, (iy + 0.5) * CELL_SIZE)
                    if point_segment_distance(candidate, start, end) <= SWATH_RADIUS:
                        cells.add((ix, iy))
    return cells


def build(tracks_path: Path, licences_path: Path, output: Path) -> None:
    tracks = json.loads(tracks_path.read_text(encoding="utf-8"))["features"]
    licence_features = json.loads(licences_path.read_text(encoding="utf-8"))["features"]
    licences = {feature.get("properties", {}).get("LICENSE"): feature for feature in licence_features}
    result = {
        "project": "Multi-project operations",
        "scope": "project_operations",
        "dataType": "actual_coverage_summary",
        "method": "Verified DJI flight trajectory buffered by 25 m (50 m swath), 10 m grid, clipped to each project licence polygon.",
        "cellSizeM": CELL_SIZE,
        "swathWidthM": SWATH_RADIUS * 2,
        "projects": {},
    }
    for project_key, licence_id in PROJECT_LICENCES.items():
        project_tracks = [feature for feature in tracks if feature.get("properties", {}).get("projectKey") == project_key]
        if not project_tracks:
            continue
        points = [point for feature in project_tracks for point in feature["geometry"]["coordinates"]]
        lon0 = sum(point[0] for point in points) / len(points)
        lat0 = sum(point[1] for point in points) / len(points)
        sensor_cells = {}
        sensor_daily = {}
        for feature in project_tracks:
            props = feature["properties"]
            sensor, date_value = props["sensor"], props["date"]
            cells = coverage_cells(feature, lon0, lat0)
            sensor_cells.setdefault(sensor, set()).update(cells)
            sensor_daily.setdefault(sensor, {}).setdefault(date_value, set()).update(cells)
        licence = licences[licence_id]["geometry"]
        cos_lat0 = math.cos(math.radians(lat0))

        def cell_area(cells):
            return sum(
                CELL_SIZE * CELL_SIZE
                for ix, iy in cells
                if point_in_geometry((
                    lon0 + ((ix + 0.5) * CELL_SIZE) / (METRES_PER_DEGREE * cos_lat0),
                    lat0 + ((iy + 0.5) * CELL_SIZE) / METRES_PER_DEGREE,
                ), licence)
            )

        sensors = {}
        for sensor, cells in sorted(sensor_cells.items()):
            daily = sensor_daily[sensor]
            sensors[sensor] = {
                "dates": sorted(daily),
                "acquisitionCount": sum(1 for feature in project_tracks if feature["properties"]["sensor"] == sensor),
                "scopes": {"licence": {
                    "label": next(feature["properties"].get("area") for feature in project_tracks if feature["properties"]["sensor"] == sensor),
                    "totalAreaM2": cell_area(cells),
                    "dailyAreaM2": {date_value: cell_area(date_cells) for date_value, date_cells in sorted(daily.items())},
                }},
            }
        result["projects"][project_key] = {"licence": licence_id, "sensors": sensors}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        key: {sensor: value["acquisitionCount"] for sensor, value in project["sensors"].items()}
        for key, project in result["projects"].items()
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tracks", type=Path)
    parser.add_argument("--licences", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.tracks, args.licences, args.output)


if __name__ == "__main__":
    main()
