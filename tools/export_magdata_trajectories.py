"""Extract 1 Hz GNSS trajectories from MagArrow .magdata containers.

The proprietary container embeds intact NMEA GGA/RMC sentences.  This tool
only reads those navigation sentences; it does not decode or alter magnetic
measurements.  Coverage is an explicitly labelled 50 m swath estimate,
calculated on a 10 m grid and clipped to the licence/uchastik polygons.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from pathlib import Path


GGA = re.compile(rb"\$[A-Z]{2}GGA,[^\x00\r\n$]{10,180}\*[0-9A-Fa-f]{2}")
RMC = re.compile(rb"\$[A-Z]{2}RMC,[^\x00\r\n$]{10,180}\*[0-9A-Fa-f]{2}")
DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})__(.+)$")
ACQUISITION = re.compile(r"SRVY\d+-ACQU\d+(?:\s*\(\d+\))?", re.IGNORECASE)
LON0 = 113.5
LAT0 = 49.15
METRES_PER_DEGREE = 111_320.0
COS_LAT0 = math.cos(math.radians(LAT0))
CELL_SIZE = 10.0
SWATH_RADIUS = 25.0


def nmea_coordinate(value: str, hemisphere: str, longitude: bool) -> float:
    degree_digits = 3 if longitude else 2
    degrees = int(value[:degree_digits])
    minutes = float(value[degree_digits:])
    result = degrees + minutes / 60.0
    return -result if hemisphere in {"S", "W"} else result


def rmc_dates(data: bytes) -> dict[str, str]:
    dates = {}
    for raw in RMC.findall(data):
        fields = raw.decode("ascii", "ignore").split("*")[0].split(",")
        if len(fields) < 10 or len(fields[1]) < 6 or len(fields[9]) != 6:
            continue
        try:
            parsed = dt.datetime.strptime(fields[9], "%d%m%y").date().isoformat()
        except ValueError:
            continue
        dates[fields[1][:6]] = parsed
    return dates


def extract_points(path: Path, fallback_date: str) -> tuple[list[list[float]], str, str]:
    data = path.read_bytes()
    dates = rmc_dates(data)
    points = []
    timestamps = []
    for raw in GGA.findall(data):
        fields = raw.decode("ascii", "ignore").split("*")[0].split(",")
        if len(fields) < 10 or not fields[2] or not fields[4]:
            continue
        try:
            if int(fields[6] or 0) <= 0:
                continue
            lat = nmea_coordinate(fields[2], fields[3], False)
            lon = nmea_coordinate(fields[4], fields[5], True)
        except (ValueError, IndexError):
            continue
        if not (112.0 <= lon <= 115.0 and 47.0 <= lat <= 51.0):
            continue
        time_value = fields[1]
        date_value = dates.get(time_value[:6], fallback_date)
        canonical = f"{date_value}T{time_value[:2]}:{time_value[2:4]}:{time_value[4:6]}Z"
        if not points or abs(lon - points[-1][0]) > 1e-10 or abs(lat - points[-1][1]) > 1e-10:
            points.append([round(lon, 8), round(lat, 8)])
            timestamps.append(canonical)
    if len(points) < 2:
        raise ValueError(f"{path.name}: fewer than two valid NMEA GGA points")
    return points, timestamps[0], timestamps[-1]


def project(point: list[float]) -> tuple[float, float]:
    return (
        (point[0] - LON0) * METRES_PER_DEGREE * COS_LAT0,
        (point[1] - LAT0) * METRES_PER_DEGREE,
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


def rdp(points: list[list[float]], tolerance_m: float = 2.5) -> list[list[float]]:
    if len(points) <= 2:
        return points
    projected = [project(point) for point in points]

    def simplify(start: int, end: int) -> list[int]:
        if end <= start + 1:
            return [start, end]
        furthest, distance = None, -1.0
        for index in range(start + 1, end):
            candidate = point_segment_distance(projected[index], projected[start], projected[end])
            if candidate > distance:
                furthest, distance = index, candidate
        if distance <= tolerance_m:
            return [start, end]
        left = simplify(start, furthest)
        right = simplify(furthest, end)
        return left[:-1] + right

    return [points[index] for index in simplify(0, len(points) - 1)]


def parse_file(path: Path, output_dir: Path) -> Path:
    match = DATE_PREFIX.match(path.name)
    if not match:
        raise ValueError(f"{path.name}: expected YYYY-MM-DD__ source prefix")
    fallback_date, source_name = match.groups()
    acquisition_match = ACQUISITION.search(source_name)
    acquisition = acquisition_match.group(0).upper().replace(" ", "") if acquisition_match else Path(source_name).stem
    points, started, ended = extract_points(path, fallback_date)
    payload = {
        "type": "Feature",
        "id": f"magarrow-actual-{re.sub(r'[^a-z0-9]+', '-', acquisition.lower()).strip('-')}",
        "properties": {
            "project": "Nergui Undur",
            "area": "Nergui Undur",
            "sensor": "MagArrow",
            "acquisition": acquisition,
            "dataType": "actual_flight_track",
            "plannedActual": "actual",
            "status": "confirmed_source",
            "date": fallback_date,
            "startTime": started,
            "endTime": ended,
            "sourceFile": source_name,
            "sourceUrl": "https://drive.google.com/drive/folders/1ctZs3rBgYtqIL9fa7J_C_L_y3LLSwXfX",
            "sourceCrs": "EPSG:4326",
            "displayCrs": "EPSG:4326",
            "crsVerified": True,
            "pointCount": len(points),
            "nmeaSource": "embedded GGA/RMC",
        },
        "geometry": {"type": "LineString", "coordinates": rdp(points)},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{path.stem}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{source_name}: {len(points)} GGA points -> {len(payload['geometry']['coordinates'])} display points")
    return target


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
    for polygon in polygons:
        if polygon and point_in_ring(point, polygon[0]) and not any(point_in_ring(point, hole) for hole in polygon[1:]):
            return True
    return False


def coverage_cells(feature: dict) -> set[tuple[int, int]]:
    points = [project(point) for point in feature["geometry"]["coordinates"]]
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


def build(parsed_dir: Path, licence_path: Path, uchastik_path: Path, tracks_output: Path, stats_output: Path) -> None:
    features = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(parsed_dir.glob("*.json"))]
    if not features:
        raise ValueError(f"No parsed trajectories in {parsed_dir}")
    daily_cells: dict[str, set[tuple[int, int]]] = {}
    all_cells: set[tuple[int, int]] = set()
    for feature in features:
        cells = coverage_cells(feature)
        date_value = feature["properties"]["date"]
        daily_cells.setdefault(date_value, set()).update(cells)
        all_cells.update(cells)

    licence = json.loads(licence_path.read_text(encoding="utf-8"))["features"][0]
    uchastiks = json.loads(uchastik_path.read_text(encoding="utf-8"))["features"]
    uchastik_all = {
        "type": "Feature",
        "id": "uchastik-all",
        "properties": {"name": "Бүх участик"},
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                polygon
                for feature in uchastiks
                for polygon in (
                    [feature["geometry"]["coordinates"]]
                    if feature["geometry"]["type"] == "Polygon"
                    else feature["geometry"]["coordinates"]
                )
            ],
        },
    }
    scopes = [("licence", licence), ("uchastik-all", uchastik_all)] + [
        (str(feature["id"]), feature) for feature in uchastiks
    ]
    stats = {
        "method": "Actual GNSS trajectory buffered by 25 m (50 m swath), 10 m grid, clipped to selected polygon.",
        "cellSizeM": CELL_SIZE,
        "swathWidthM": SWATH_RADIUS * 2,
        "dates": sorted(daily_cells),
        "acquisitionCount": len(features),
        "scopes": {},
    }
    for key, feature in scopes:
        geometry = feature["geometry"]
        def area(cells):
            return sum(
                CELL_SIZE * CELL_SIZE
                for ix, iy in cells
                if point_in_geometry((LON0 + ((ix + 0.5) * CELL_SIZE) / (METRES_PER_DEGREE * COS_LAT0), LAT0 + ((iy + 0.5) * CELL_SIZE) / METRES_PER_DEGREE), geometry)
            )
        stats["scopes"][key] = {
            "label": feature.get("properties", {}).get("name") or feature.get("properties", {}).get("AREANAME_L") or key,
            "totalAreaM2": area(all_cells),
            "dailyAreaM2": {date_value: area(cells) for date_value, cells in daily_cells.items()},
        }

    tracks_output.parent.mkdir(parents=True, exist_ok=True)
    tracks_output.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "Nergui Undur MagArrow Actual GNSS Trajectories",
        "project": "Nergui Undur",
        "sensor": "MagArrow",
        "dataType": "actual_flight_track",
        "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    stats_output.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(features)} trajectories across {len(daily_cells)} dates")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    parse_parser = subparsers.add_parser("parse")
    parse_parser.add_argument("source", type=Path)
    parse_parser.add_argument("output_dir", type=Path)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("parsed_dir", type=Path)
    build_parser.add_argument("licence", type=Path)
    build_parser.add_argument("uchastik", type=Path)
    build_parser.add_argument("tracks_output", type=Path)
    build_parser.add_argument("stats_output", type=Path)
    args = parser.parse_args()
    if args.command == "parse":
        parse_file(args.source, args.output_dir)
    else:
        build(args.parsed_dir, args.licence, args.uchastik, args.tracks_output, args.stats_output)


if __name__ == "__main__":
    main()
