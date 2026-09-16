"""Build actual MagArrow flight tracks from verified 10 Hz CSV coordinates.

The exporter is intentionally conservative.  It accepts an explicit mapping,
or recognises only common unambiguous latitude/longitude and timestamp header
pairs.  It never treats DJI mission-plan geometry as an actual track and never
guesses projected coordinates or magnetic-value columns.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
from pathlib import Path


LATITUDE_NAMES = ("latitude", "lat", "gps_lat", "gpslatitude")
LONGITUDE_NAMES = ("longitude", "lon", "lng", "gps_lon", "gpslongitude")
TIME_NAMES = ("timestamp", "datetime", "date_time", "gps_time", "time_utc", "utc_time")


def normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def choose_header(headers: list[str], explicit: str | None, candidates: tuple[str, ...], label: str) -> str:
    if explicit:
        if explicit not in headers:
            raise ValueError(f"{label} column {explicit!r} not found. Headers: {headers}")
        return explicit
    by_normalised = {normalise(header): header for header in headers}
    matches = [by_normalised[name] for name in candidates if name in by_normalised]
    if len(matches) != 1:
        raise ValueError(f"Could not uniquely identify {label}. Headers: {headers}")
    return matches[0]


def parse_time(value: str) -> tuple[dt.datetime, str]:
    text = value.strip()
    if not text:
        raise ValueError("blank timestamp")
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError:
        for pattern in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                parsed = dt.datetime.strptime(text, pattern).replace(tzinfo=dt.timezone.utc)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            raise ValueError(f"unsupported timestamp {text!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed, parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def acquisition_from_name(path: Path) -> str:
    match = re.search(r"(SRVY\d+-ACQU\d+)", path.stem, re.IGNORECASE)
    return match.group(1).upper() if match else path.stem


def read_track(path: Path, latitude: str | None, longitude: str | None, timestamp: str | None) -> dict:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path.name}: CSV has no header")
        lat_key = choose_header(reader.fieldnames, latitude, LATITUDE_NAMES, "latitude")
        lon_key = choose_header(reader.fieldnames, longitude, LONGITUDE_NAMES, "longitude")
        time_key = choose_header(reader.fieldnames, timestamp, TIME_NAMES, "timestamp")
        points = []
        for row_number, row in enumerate(reader, start=2):
            try:
                when, canonical_time = parse_time(row[time_key])
                lat = float(row[lat_key])
                lon = float(row[lon_key])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{path.name}:{row_number}: {error}") from error
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"{path.name}:{row_number}: invalid WGS84 coordinate {lat}, {lon}")
            points.append((when, canonical_time, [lon, lat]))
    if len(points) < 2:
        raise ValueError(f"{path.name}: at least two valid coordinate rows are required")
    points.sort(key=lambda item: item[0])
    acquisition = acquisition_from_name(path)
    return {
        "type": "Feature",
        "id": f"magarrow-actual-{acquisition.lower()}",
        "properties": {
            "project": "Nergui Undur",
            "area": "Heseg Uul hoid",
            "sensor": "MagArrow",
            "acquisition": acquisition,
            "dataType": "actual_flight_track",
            "plannedActual": "actual",
            "status": "confirmed",
            "date": points[0][0].date().isoformat(),
            "startTime": points[0][1],
            "endTime": points[-1][1],
            "sourceFile": path.name,
            "sourceUrl": "https://drive.google.com/drive/folders/1gUPM7-VCrJFKGRgC8mzJNcQBu9iNM8gO",
            "sourceCrs": "EPSG:4326",
            "displayCrs": "EPSG:4326",
            "crsVerified": True,
            "pointCount": len(points),
        },
        "geometry": {"type": "LineString", "coordinates": [item[2] for item in points]},
    }


def export(source_dir: Path, output: Path, latitude: str | None, longitude: str | None, timestamp: str | None) -> None:
    files = sorted(source_dir.glob("*.csv"))
    if not files:
        raise ValueError(f"No local 10 Hz CSV files found in {source_dir}. No tracks were created.")
    features = [read_track(path, latitude, longitude, timestamp) for path in files]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "MagArrow Actual Flight Tracks",
        "project": "Nergui Undur",
        "sensor": "MagArrow",
        "dataType": "actual_flight_track",
        "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Exported {len(features)} actual MagArrow acquisition track(s) to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--latitude")
    parser.add_argument("--longitude")
    parser.add_argument("--timestamp")
    arguments = parser.parse_args()
    export(arguments.source_dir, arguments.output, arguments.latitude, arguments.longitude, arguments.timestamp)
