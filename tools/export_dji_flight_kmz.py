"""Export verified DJI FlightRecord KMZ trajectories and coverage statistics.

Only LineString coordinates already present in DJI FlightRecord KMZ exports are
used. Tracker rows without a matching KMZ remain register records and are never
promoted to geometry.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
CELL_SIZE = 10.0
SWATH_RADIUS = 25.0
METRES_PER_DEGREE = 111_320.0

FLIGHTS = {
    "DJIFlightRecord_2026-06-20_[12-31-37].kmz": {
        "mission": "DJI_202606201229_028_B33",
        "trackerId": "buduunkhad:115",
        "date": "2026-06-20",
        "sensor": "L2",
        "sourceId": "1zqFcmKbkY6yocNR-x5EHaDmb0OpUqYHT",
    },
    "DJIFlightRecord_2026-06-20_[13-05-55].kmz": {
        "mission": "DJI_202606201304_029_B33",
        "trackerId": "buduunkhad:116",
        "date": "2026-06-20",
        "sensor": "L2",
        "sourceId": "15PfSq4iBqASovU-u5dRXnkG2-scYVg3C",
    },
    "DJIFlightRecord_2026-06-20_[13-54-39].kmz": {
        "mission": "DJI_202606201352_030_B33",
        "trackerId": "buduunkhad:117",
        "date": "2026-06-20",
        "sensor": "L2",
        "sourceId": "1Cj2DBsXVBpuA2bsRrCkEcPCPlnT3QPlo",
    },
}


def local_project(point: list[float], lon0: float, lat0: float) -> tuple[float, float]:
    return (
        (point[0] - lon0) * METRES_PER_DEGREE * math.cos(math.radians(lat0)),
        (point[1] - lat0) * METRES_PER_DEGREE,
    )


def point_segment_distance(point, start, end) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify(points: list[list[float]], lon0: float, lat0: float, tolerance_m: float = 2.5) -> list[list[float]]:
    if len(points) <= 2:
        return points
    projected = [local_project(point, lon0, lat0) for point in points]

    def rdp(start: int, end: int) -> list[int]:
        if end <= start + 1:
            return [start, end]
        furthest, distance = None, -1.0
        for index in range(start + 1, end):
            candidate = point_segment_distance(projected[index], projected[start], projected[end])
            if candidate > distance:
                furthest, distance = index, candidate
        if distance <= tolerance_m:
            return [start, end]
        left = rdp(start, furthest)
        right = rdp(furthest, end)
        return left[:-1] + right

    return [points[index] for index in rdp(0, len(points) - 1)]


def parse_kmz(path: Path) -> list[list[float]]:
    with zipfile.ZipFile(path) as archive:
        kml_name = next(name for name in archive.namelist() if name.lower().endswith(".kml"))
        root = ET.fromstring(archive.read(kml_name))
    candidates = []
    for node in root.findall(".//kml:LineString/kml:coordinates", KML_NS):
        points = []
        for value in (node.text or "").split():
            parts = value.split(",")
            if len(parts) < 2:
                continue
            lon, lat = float(parts[0]), float(parts[1])
            if 90 <= lon <= 110 and 40 <= lat <= 55:
                point = [round(lon, 8), round(lat, 8)]
                if not points or point != points[-1]:
                    points.append(point)
        if len(points) >= 2:
            candidates.append(points)
    if not candidates:
        raise ValueError(f"{path.name}: no valid KML LineString")
    return max(candidates, key=len)


def point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
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


def point_in_geometry(point: tuple[float, float], geometry: dict) -> bool:
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry.get("coordinates", [])
    return any(
        polygon and point_in_ring(point, polygon[0])
        and not any(point_in_ring(point, hole) for hole in polygon[1:])
        for polygon in polygons
    )


def coverage_cells(feature: dict, lon0: float, lat0: float) -> set[tuple[int, int]]:
    points = [local_project(point, lon0, lat0) for point in feature["geometry"]["coordinates"]]
    cells: set[tuple[int, int]] = set()
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
            for offset_x in range(-radius_cells, radius_cells + 1):
                for offset_y in range(-radius_cells, radius_cells + 1):
                    ix, iy = centre_x + offset_x, centre_y + offset_y
                    candidate = ((ix + 0.5) * CELL_SIZE, (iy + 0.5) * CELL_SIZE)
                    if point_segment_distance(candidate, start, end) <= SWATH_RADIUS:
                        cells.add((ix, iy))
    return cells


def build(source_dir: Path, licences_path: Path, tracks_output: Path, coverage_output: Path) -> None:
    source_paths = [source_dir / name for name in FLIGHTS]
    missing = [path.name for path in source_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing KMZ: {', '.join(missing)}")

    raw = {path.name: parse_kmz(path) for path in source_paths}
    all_points = [point for points in raw.values() for point in points]
    lon0 = sum(point[0] for point in all_points) / len(all_points)
    lat0 = sum(point[1] for point in all_points) / len(all_points)
    features = []
    for path in source_paths:
        metadata = FLIGHTS[path.name]
        points = raw[path.name]
        started = re.search(r"\[(\d{2})-(\d{2})-(\d{2})\]", path.name)
        started_value = f"{metadata['date']}T{':'.join(started.groups())}+08:00" if started else None
        feature = {
            "type": "Feature",
            "id": f"buduunkhad-{metadata['mission'].lower().replace('_', '-')}",
            "properties": {
                "project": "Buduunkhad",
                "projectKey": "buduunkhad",
                "area": "Бүдүүн хад",
                "licence": "XV-023222",
                "sensor": metadata["sensor"],
                "mission": metadata["mission"],
                "trackerId": metadata["trackerId"],
                "dataType": "actual_flight_track",
                "plannedActual": "actual",
                "status": "confirmed_source",
                "contextOnly": True,
                "trajectoryAvailable": True,
                "date": metadata["date"],
                "startTime": started_value,
                "sourceFile": path.name,
                "sourceUrl": f"https://drive.google.com/file/d/{metadata['sourceId']}/view",
                "sourceKind": "DJI FlightRecord trajectory export",
                "flightRecordVerified": True,
                "sourceCrs": "EPSG:4326",
                "displayCrs": "EPSG:4326",
                "crsVerified": True,
                "pointCount": len(points),
                "coverageSwathWidthM": SWATH_RADIUS * 2,
            },
            "geometry": {"type": "LineString", "coordinates": simplify(points, lon0, lat0)},
        }
        features.append(feature)

    licences = json.loads(licences_path.read_text(encoding="utf-8"))
    licence = next(
        feature for feature in licences["features"]
        if feature.get("properties", {}).get("LICENSE") == "XV-023222"
    )
    sensor_cells: dict[str, set[tuple[int, int]]] = {}
    sensor_daily: dict[str, dict[str, set[tuple[int, int]]]] = {}
    for feature in features:
        sensor = feature["properties"]["sensor"]
        date_value = feature["properties"]["date"]
        cells = coverage_cells(feature, lon0, lat0)
        sensor_cells.setdefault(sensor, set()).update(cells)
        sensor_daily.setdefault(sensor, {}).setdefault(date_value, set()).update(cells)

    def cell_area(cells: set[tuple[int, int]]) -> float:
        cos_lat0 = math.cos(math.radians(lat0))
        return sum(
            CELL_SIZE * CELL_SIZE
            for ix, iy in cells
            if point_in_geometry((
                lon0 + ((ix + 0.5) * CELL_SIZE) / (METRES_PER_DEGREE * cos_lat0),
                lat0 + ((iy + 0.5) * CELL_SIZE) / METRES_PER_DEGREE,
            ), licence["geometry"])
        )

    coverage = {
        "project": "Multi-project operations",
        "scope": "project_operations",
        "dataType": "actual_coverage_summary",
        "method": "Verified DJI FlightRecord trajectory buffered by 25 m (50 m swath), 10 m grid, clipped to licence polygon.",
        "cellSizeM": CELL_SIZE,
        "swathWidthM": SWATH_RADIUS * 2,
        "projects": {"buduunkhad": {"licence": "XV-023222", "sensors": {}}},
    }
    for sensor, cells in sensor_cells.items():
        daily = sensor_daily[sensor]
        coverage["projects"]["buduunkhad"]["sensors"][sensor] = {
            "dates": sorted(daily),
            "acquisitionCount": sum(1 for feature in features if feature["properties"]["sensor"] == sensor),
            "scopes": {"licence": {
                "label": "Бүдүүн хад",
                "totalAreaM2": cell_area(cells),
                "dailyAreaM2": {date_value: cell_area(date_cells) for date_value, date_cells in daily.items()},
            }},
        }

    tracks_output.parent.mkdir(parents=True, exist_ok=True)
    tracks_output.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "Verified project DJI FlightRecord trajectories",
        "project": "Multi-project operations",
        "scope": "project_operations",
        "dataType": "actual_flight_track",
        "featureCount": len(features),
        "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    coverage_output.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(features)} verified trajectories and coverage for {', '.join(sorted(sensor_cells))}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--licences", required=True, type=Path)
    parser.add_argument("--tracks-output", required=True, type=Path)
    parser.add_argument("--coverage-output", required=True, type=Path)
    args = parser.parse_args()
    build(args.source_dir, args.licences, args.tracks_output, args.coverage_output)


if __name__ == "__main__":
    main()
