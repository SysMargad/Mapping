"""Import verified project flight trajectories and match them to tracker missions.

The importer never creates geometry from tracker rows alone. A track is emitted
only when a coordinate-bearing MRK, KML, KMZ, CSV, or DJIFlightRecord TXT file
can be matched to one tracker mission and its coordinates fall inside the
matching project licence extent.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coordinate_sources as cs  # noqa: E402


SUPPORTED_SUFFIXES = {".mrk", ".kml", ".kmz", ".csv", ".txt"}
PROJECTS = {
    "hetsuu-hutul": {"project": "Hetsuu hutul", "area": "Хэцүү хөтөл", "licence": "XV-022905"},
    "artsat": {"project": "Artsat", "area": "Арцат", "licence": "XV-021395"},
    "buduunkhad": {"project": "Buduunkhad", "area": "Бүдүүн хад", "licence": "XV-023222"},
}
MISSION_TIME_RE = re.compile(r"DJI_(\d{8})(\d{4,6})", re.IGNORECASE)
FLIGHT_RECORD_RE = re.compile(
    r"DJIFlightRecord[_-](\d{4})-(\d{2})-(\d{2})[_-]\[(\d{2})-(\d{2})-(\d{2})\]",
    re.IGNORECASE,
)
KML_NS = {"kml": "http://www.opengis.net/kml/2.2", "gx": "http://www.google.com/kml/ext/2.2"}
METRES_PER_DEGREE = 111_320.0


def normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def mission_time(value: str) -> datetime | None:
    match = MISSION_TIME_RE.search(value)
    if not match:
        return None
    clock = match.group(2).ljust(6, "0")
    return datetime.strptime(match.group(1) + clock, "%Y%m%d%H%M%S")


def file_time(path: Path) -> datetime | None:
    match = MISSION_TIME_RE.search(path.name)
    if match:
        return mission_time(match.group(0))
    match = FLIGHT_RECORD_RE.search(path.name)
    if not match:
        return None
    return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")


def iter_coordinate_pairs(value):
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        yield float(value[0]), float(value[1])
    elif isinstance(value, list):
        for item in value:
            yield from iter_coordinate_pairs(item)


def licence_extents(path: Path) -> dict[str, tuple[float, float, float, float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    by_licence = {value["licence"]: key for key, value in PROJECTS.items()}
    result = {}
    for feature in data.get("features", []):
        project_key = by_licence.get(feature.get("properties", {}).get("LICENSE"))
        if not project_key:
            continue
        points = list(iter_coordinate_pairs((feature.get("geometry") or {}).get("coordinates")))
        if points:
            result[project_key] = (
                min(point[0] for point in points), min(point[1] for point in points),
                max(point[0] for point in points), max(point[1] for point in points),
            )
    return result


def in_extent(point: list[float], extent, margin: float = 0.02) -> bool:
    west, south, east, north = extent
    return west - margin <= point[0] <= east + margin and south - margin <= point[1] <= north + margin


def parse_kml_bytes(content: bytes) -> list[list[float]]:
    root = ET.fromstring(content)
    candidates = []
    for node in root.findall(".//kml:LineString/kml:coordinates", KML_NS):
        points = []
        for value in (node.text or "").split():
            parts = value.split(",")
            if len(parts) >= 2:
                points.append([float(parts[0]), float(parts[1])])
        if len(points) >= 2:
            candidates.append(points)
    gx_points = []
    for node in root.findall(".//gx:Track/gx:coord", KML_NS):
        parts = (node.text or "").split()
        if len(parts) >= 2:
            gx_points.append([float(parts[0]), float(parts[1])])
    if len(gx_points) >= 2:
        candidates.append(gx_points)
    if not candidates:
        point_sequence = []
        for node in root.findall(".//kml:Point/kml:coordinates", KML_NS):
            parts = (node.text or "").strip().split(",")
            if len(parts) >= 2:
                point_sequence.append([float(parts[0]), float(parts[1])])
        if len(point_sequence) >= 2:
            candidates.append(point_sequence)
    if not candidates:
        raise ValueError("no coordinate sequence in KML")
    return max(candidates, key=len)


class UnsupportedFlightLog(ValueError):
    """A DJI flight log that this importer cannot decode into coordinates."""


def is_binary_flight_log(payload: bytes) -> bool:
    """DJIFlightRecord .txt is an encoded container, not a text table.

    A .txt extension says nothing about the contents, so the bytes decide.
    """
    head = payload[:4096]
    if not head:
        return False
    if head[:1] == b"\x55" or head[:4] in {b"PK\x03\x04", b"\x1f\x8b\x08\x00"}:
        return True
    if b"\x00" in head:
        return True
    printable = sum(1 for byte in head if 9 <= byte <= 13 or 32 <= byte <= 126 or byte >= 128)
    return printable / len(head) < 0.9


def parse_delimited_text(text: str) -> list[list[float]]:
    table = cs.parse_table(text)
    if len(table.points) < 2:
        raise ValueError("fewer than two coordinate rows")
    return [[point.lon, point.lat] for point in table.points]


def parse_mrk(text: str) -> list[list[float]]:
    points = []
    hemisphere = re.compile(r"([+-]?\d{1,3}(?:\.\d+)?)\s*[, ]?\s*([NSEW])\b", re.IGNORECASE)
    decimal = re.compile(r"[+-]?\d{1,3}\.\d+")
    for line in text.splitlines():
        lat = lon = None
        for raw, direction in hemisphere.findall(line):
            value = float(raw)
            if direction.upper() in {"S", "W"}:
                value *= -1
            if direction.upper() in {"N", "S"}:
                lat = value
            else:
                lon = value
        if lat is None or lon is None:
            values = [float(value) for value in decimal.findall(line)]
            lat = next((value for value in values if 40 <= value <= 55), None)
            lon = next((value for value in values if 90 <= value <= 110), None)
        if lat is not None and lon is not None:
            points.append([lon, lat])
    if len(points) < 2:
        raise ValueError("fewer than two MRK positions")
    return points


def parse_source(path: Path) -> list[list[float]]:
    suffix = path.suffix.lower()
    if suffix == ".kmz":
        with zipfile.ZipFile(path) as archive:
            name = next((name for name in archive.namelist() if name.lower().endswith(".kml")), None)
            if not name:
                raise ValueError("KMZ has no KML")
            return parse_kml_bytes(archive.read(name))
    if suffix == ".kml":
        return parse_kml_bytes(path.read_bytes())
    payload = path.read_bytes()
    if suffix == ".txt" and is_binary_flight_log(payload):
        raise UnsupportedFlightLog(
            "DJI flight log is a binary/encoded container; export coordinates before import"
        )
    text = payload.decode("utf-8-sig", errors="replace")
    if suffix == ".mrk":
        return parse_mrk(text)
    return parse_delimited_text(text)


def rounded_stats(values: list[float], prefix: str, column: str, reference: str) -> dict:
    finite = [value for value in values if math.isfinite(value) and -1000 <= value <= 10000]
    if not finite:
        return {}
    return {
        f"{prefix}MinM": round(min(finite), 2),
        f"{prefix}MaxM": round(max(finite), 2),
        f"{prefix}MeanM": round(sum(finite) / len(finite), 2),
        f"{prefix}SampleCount": len(finite),
        f"{prefix}Column": column,
        f"{prefix}Reference": reference,
    }


def kml_altitudes(content: bytes) -> list[float]:
    root = ET.fromstring(content)
    values = []
    for node in root.findall(".//kml:coordinates", KML_NS):
        for coordinate in (node.text or "").split():
            parts = coordinate.split(",")
            if len(parts) >= 3:
                try:
                    values.append(float(parts[2]))
                except ValueError:
                    pass
    for node in root.findall(".//gx:coord", KML_NS):
        parts = (node.text or "").split()
        if len(parts) >= 3:
            try:
                values.append(float(parts[2]))
            except ValueError:
                pass
    return values


def delimited_altitude_stats(text: str) -> dict:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = csv.reader(io.StringIO(text), dialect)
    try:
        raw_headers = next(rows)
    except StopIteration:
        return {}
    headers = [normalise(value) for value in raw_headers]

    def find_column(candidates: tuple[str, ...], excluded: tuple[str, ...] = ()) -> int | None:
        for candidate in candidates:
            for index, header in enumerate(headers):
                if any(value in header for value in excluded):
                    continue
                if header == candidate or header.startswith(candidate):
                    return index
        return None

    absolute_index = find_column(
        ("altitude", "gpsaltitude", "heightoverellipsoid", "elevation"),
        ("relative", "agl", "80m"),
    )
    relative_index = find_column(
        ("relativealtitude", "heightagl", "altitudeagl", "flightheight", "relativeheight", "height"),
        ("ellipsoid",),
    )
    # A generic Height column is often relative flight height. Do not reuse an
    # explicitly selected absolute column as relative height.
    if relative_index == absolute_index:
        relative_index = None
    absolute_values, relative_values = [], []
    for row in rows:
        if absolute_index is not None:
            try:
                absolute_values.append(float(row[absolute_index]))
            except (IndexError, TypeError, ValueError):
                pass
        if relative_index is not None:
            try:
                relative_values.append(float(row[relative_index]))
            except (IndexError, TypeError, ValueError):
                pass
    result = {}
    if absolute_index is not None:
        result.update(rounded_stats(
            absolute_values,
            "sourceAltitude",
            raw_headers[absolute_index].strip(),
            "source-provided GNSS/absolute altitude",
        ))
    if relative_index is not None:
        result.update(rounded_stats(
            relative_values,
            "flightHeight",
            raw_headers[relative_index].strip(),
            "source-provided relative/AGL height",
        ))
    return result


def source_altitude_stats(path: Path) -> dict:
    """Extract source-provided altitude without inferring AGL from terrain."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".kmz":
            with zipfile.ZipFile(path) as archive:
                name = next((name for name in archive.namelist() if name.lower().endswith(".kml")), None)
                if not name:
                    return {}
                return rounded_stats(kml_altitudes(archive.read(name)), "sourceAltitude", "KML altitude", "KML altitude")
        if suffix == ".kml":
            return rounded_stats(kml_altitudes(path.read_bytes()), "sourceAltitude", "KML altitude", "KML altitude")
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if suffix in {".csv", ".txt"}:
            return delimited_altitude_stats(text)
        if suffix == ".mrk":
            values = []
            pattern = re.compile(r"(?:ellh|alt(?:itude)?|height)\s*[:=,\t ]+([+-]?\d+(?:\.\d+)?)", re.IGNORECASE)
            for line in text.splitlines():
                match = pattern.search(line)
                if match:
                    values.append(float(match.group(1)))
            return rounded_stats(values, "sourceAltitude", "MRK altitude", "MRK source altitude")
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile):
        return {}
    return {}


class OutsideLicenceExtent(ValueError):
    """Coordinates do not belong to this project's licence area."""


def clean_points(points: list[list[float]], extent) -> list[list[float]]:
    cleaned = []
    for lon, lat in points:
        point = [round(lon, 8), round(lat, 8)]
        if not (-180 <= lon <= 180 and -90 <= lat <= 90) or not in_extent(point, extent):
            continue
        if not cleaned or point != cleaned[-1]:
            cleaned.append(point)
    if len(cleaned) < 2:
        raise OutsideLicenceExtent("coordinates are outside the project licence extent")
    return cleaned


def simplify(points: list[list[float]], tolerance_m: float = 2.5) -> list[list[float]]:
    if len(points) <= 2:
        return points
    lon0 = sum(point[0] for point in points) / len(points)
    lat0 = sum(point[1] for point in points) / len(points)
    projected = [
        ((point[0] - lon0) * METRES_PER_DEGREE * math.cos(math.radians(lat0)),
         (point[1] - lat0) * METRES_PER_DEGREE)
        for point in points
    ]

    def distance(point, start, end):
        px, py = point
        ax, ay = start
        bx, by = end
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    def rdp(start, end):
        if end <= start + 1:
            return [start, end]
        furthest, maximum = None, -1.0
        for index in range(start + 1, end):
            candidate = distance(projected[index], projected[start], projected[end])
            if candidate > maximum:
                furthest, maximum = index, candidate
        if maximum <= tolerance_m:
            return [start, end]
        return rdp(start, furthest)[:-1] + rdp(furthest, end)

    return [points[index] for index in rdp(0, len(points) - 1)]


def load_mapping(path: Path | None) -> dict[str, dict]:
    if not path:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("sources", data) if isinstance(data, dict) else data
    result = {}
    for entry in entries:
        key = normalise(entry.get("file", ""))
        if key:
            result[key] = entry
    return result


def match_record(path: Path, records: list[dict], mapping: dict[str, dict]) -> tuple[dict | None, dict, str]:
    """Resolve one source file to at most one tracker mission.

    Returns (record, source config, reason). An explicit mapping wins, then a
    unique exact mission name, then a single unambiguous same-day flight. When
    two flights on the same day are equally close the file is left unmatched
    rather than assigned to a guess.
    """
    config = mapping.get(normalise(path.name), {})
    if config.get("trackerId"):
        matches = [record for record in records if record.get("id") == config["trackerId"]]
        if len(matches) == 1:
            return matches[0], config, cs.IMPORTED
        return None, config, cs.AMBIGUOUS_MATCH if matches else cs.MISSION_UNMATCHED
    if config.get("mission"):
        matches = [record for record in records if record.get("mission") == config["mission"]]
        if len(matches) == 1:
            return matches[0], config, cs.IMPORTED
        return None, config, cs.AMBIGUOUS_MATCH if matches else cs.MISSION_UNMATCHED
    filename_key = normalise(path.stem)
    exact = [record for record in records if normalise(record.get("mission", "")) in filename_key]
    if len(exact) == 1:
        return exact[0], config, cs.IMPORTED
    if len(exact) > 1:
        return None, config, cs.AMBIGUOUS_MATCH
    started = file_time(path)
    if not started:
        return None, config, cs.MISSION_UNMATCHED
    same_day = []
    for record in records:
        candidate = mission_time(record.get("mission", ""))
        if candidate and candidate.date() == started.date():
            same_day.append((abs((candidate - started).total_seconds()), record))
    same_day.sort(key=lambda item: item[0])
    if not same_day:
        return None, config, cs.MISSION_UNMATCHED
    if same_day[0][0] > 20 * 60:
        return None, config, cs.DATE_MISMATCH
    # More than one flight that day sits within the separation window, so the
    # nearest-in-time rule cannot pick a winner on its own.
    if len(same_day) > 1 and same_day[1][0] - same_day[0][0] < 3 * 60:
        return None, config, cs.AMBIGUOUS_MATCH
    return same_day[0][1], config, cs.IMPORTED


def build(args) -> dict:
    trackers = json.loads(args.trackers.read_text(encoding="utf-8"))
    records = [record for record in trackers.get("records", []) if record.get("projectKey") == args.project]
    extent = licence_extents(args.licences).get(args.project)
    if not extent:
        raise ValueError(f"No licence extent for {args.project}")
    mapping = load_mapping(args.mapping)
    existing = json.loads(args.existing.read_text(encoding="utf-8")) if args.existing.exists() else {"type": "FeatureCollection", "features": []}
    features = list(existing.get("features", []))
    imported, unmatched, rejected = [], [], []
    audit = []
    seen_tracker_ids = set()

    def note(path, code, detail, tracker_id=None):
        item = {"file": path.name, "reason": code, "detail": detail}
        if tracker_id:
            item["trackerId"] = tracker_id
        audit.append(item)

    for path in sorted(item for item in args.source_dir.rglob("*") if item.suffix.lower() in SUPPORTED_SUFFIXES):
        record, source_config, reason = match_record(path, records, mapping)
        if not record:
            unmatched.append(str(path.relative_to(args.source_dir)))
            note(path, reason, "no single tracker mission could be resolved")
            continue
        if record["id"] in seen_tracker_ids:
            rejected.append({"file": path.name, "reason": f"duplicate tracker match {record['id']}"})
            note(path, cs.DUPLICATE_SOURCE, f"tracker mission {record['id']} already has a source", record["id"])
            continue
        try:
            raw_points = parse_source(path)
            points = clean_points(raw_points, extent)
        except UnsupportedFlightLog as error:
            rejected.append({"file": path.name, "trackerId": record["id"], "reason": str(error)})
            note(path, cs.UNSUPPORTED_FLIGHT_LOG, str(error), record["id"])
            continue
        except OutsideLicenceExtent as error:
            rejected.append({"file": path.name, "trackerId": record["id"], "reason": str(error)})
            note(path, cs.WRONG_PROJECT_LOCATION, str(error), record["id"])
            continue
        except Exception as error:
            rejected.append({"file": path.name, "trackerId": record["id"], "reason": str(error)})
            note(path, cs.INVALID_COORDINATES, str(error), record["id"])
            continue
        note(path, cs.IMPORTED, f"{len(points)} points inside the licence extent", record["id"])
        seen_tracker_ids.add(record["id"])
        project = PROJECTS[args.project]
        source_url = source_config.get("sourceUrl")
        feature = {
            "type": "Feature",
            "id": f"{args.project}-{record['id'].split(':')[-1]}-{normalise(record['mission'])}",
            "properties": {
                "project": project["project"], "projectKey": args.project,
                "area": record.get("area") or project["area"], "licence": project["licence"],
                "sensor": record.get("sensor", "Unknown"), "mission": record["mission"],
                "trackerId": record["id"], "date": record.get("date"),
                "dataType": "actual_flight_track", "plannedActual": "actual",
                "status": "confirmed_source", "contextOnly": True, "trajectoryAvailable": True,
                "sourceFile": path.name, "sourceKind": f"{path.suffix.upper()[1:]} coordinate trajectory",
                "flightRecordVerified": True, "sourceCrs": "EPSG:4326", "displayCrs": "EPSG:4326",
                "crsVerified": True, "pointCount": len(raw_points), "coverageSwathWidthM": 50,
                **source_altitude_stats(path),
                **({"sourceUrl": source_url} if source_url else {}),
            },
            "geometry": {"type": "LineString", "coordinates": simplify(points)},
        }
        imported.append(feature)
    replaced_tracker_ids = {feature["properties"]["trackerId"] for feature in imported}
    features = [
        feature for feature in features
        if feature.get("properties", {}).get("trackerId") not in replaced_tracker_ids
    ]
    features.extend(imported)
    output = {
        "type": "FeatureCollection", "name": "Verified project flight trajectories",
        "project": "Multi-project operations", "scope": "project_operations",
        "dataType": "actual_flight_track", "featureCount": len(features), "features": features,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    reason_counts: dict[str, int] = {}
    for item in audit:
        reason_counts[item["reason"]] = reason_counts.get(item["reason"], 0) + 1
    report = {
        "project": args.project,
        "imported": len(imported),
        "trackerRecords": len(records),
        "sourceFilesSeen": len(audit),
        "reasonCounts": dict(sorted(reason_counts.items())),
        "unmatched": unmatched,
        "rejected": rejected,
        "audit": audit,
    }
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--project", choices=sorted(PROJECTS), default="hetsuu-hutul")
    parser.add_argument("--trackers", required=True, type=Path)
    parser.add_argument("--licences", required=True, type=Path)
    parser.add_argument("--existing", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
