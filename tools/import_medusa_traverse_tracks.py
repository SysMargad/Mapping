"""Import QC-cleaned Medusa traverse points as verified project tracks.

The processed Medusa table is independent of the DJI mission tracker.  It is
therefore grouped by its own session and line identifiers and merged into the
project flight-track asset without inventing tracker mission links.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

from import_project_flight_tracks import clean_points, licence_extents, simplify


DATASET_ID = "artsat-medusa-2026-09-12-13"
SOURCE_URL = "https://drive.google.com/file/d/1UeLUScaJZt4P29nRX8d-RApjjfIsJCMG/view"
REQUIRED_COLUMNS = {
    "session", "line_id", "survey_class", "datetime_local_utc8",
    "gps_Lon", "gps_Lat", "lidar_lheight", "hard_qc_pass",
}


def truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def height_stats(rows: list[dict]) -> dict:
    values = []
    for row in rows:
        try:
            value = float(row["lidar_lheight"])
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and -1000 <= value <= 10000:
            values.append(value)
    if not values:
        return {}
    return {
        "flightHeightMinM": round(min(values), 2),
        "flightHeightMaxM": round(max(values), 2),
        "flightHeightMeanM": round(sum(values) / len(values), 2),
        "flightHeightSampleCount": len(values),
        "flightHeightColumn": "lidar_lheight",
        "flightHeightReference": "Medusa ALT-100 LiDAR AGL",
    }


def load_groups(source: Path) -> dict[tuple[str, str], list[dict]]:
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing required Medusa columns: {', '.join(sorted(missing))}")
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in reader:
            if row["survey_class"].strip().upper() != "TRAVERSE" or not truthy(row["hard_qc_pass"]):
                continue
            groups[(row["session"].strip(), row["line_id"].strip())].append(row)
    if not groups:
        raise ValueError("no QC-passed TRAVERSE rows")
    return groups


def build(source: Path, licences: Path, existing: Path, output: Path) -> dict:
    extent = licence_extents(licences).get("artsat")
    if not extent:
        raise ValueError("No licence extent for artsat")
    groups = load_groups(source)
    imported = []
    for (session, line_id), rows in sorted(groups.items()):
        rows.sort(key=lambda row: row["datetime_local_utc8"])
        raw_points = [[float(row["gps_Lon"]), float(row["gps_Lat"])] for row in rows]
        points = clean_points(raw_points, extent)
        date_value = rows[0]["datetime_local_utc8"][:10]
        imported.append({
            "type": "Feature",
            "id": f"artsat-medusa-{session.lower()}-{line_id.lower()}",
            "properties": {
                "project": "Artsat",
                "projectKey": "artsat",
                "area": "Арцат",
                "licence": "XV-021395",
                "sensor": "Medusa",
                "mission": f"Medusa MS-700 {line_id}",
                "acquisitionId": session,
                "lineId": line_id,
                "sourceDatasetId": DATASET_ID,
                "date": date_value,
                "startTime": rows[0]["datetime_local_utc8"],
                "endTime": rows[-1]["datetime_local_utc8"],
                "timeZone": "UTC+08:00",
                "dataType": "actual_flight_track",
                "plannedActual": "actual",
                "status": "confirmed_source",
                "contextOnly": True,
                "trajectoryAvailable": True,
                "sourceFile": source.name,
                "sourceUrl": SOURCE_URL,
                "sourceKind": "Medusa MS-700 QC-cleaned traverse points",
                "flightRecordVerified": True,
                "sourceCrs": "EPSG:4326",
                "displayCrs": "EPSG:4326",
                "crsVerified": True,
                "pointCount": len(raw_points),
                "simplifiedPointCount": len(simplify(points, tolerance_m=1.0)),
                "coverageStatus": "not_calculated",
                "coverageNote": "Medusa footprint/swath width is not verified, so coverage is not calculated.",
                **height_stats(rows),
            },
            "geometry": {
                "type": "LineString",
                "coordinates": simplify(points, tolerance_m=1.0),
            },
        })

    current = json.loads(existing.read_text(encoding="utf-8")) if existing.exists() else {
        "type": "FeatureCollection", "features": []
    }
    features = [
        feature for feature in current.get("features", [])
        if feature.get("properties", {}).get("sourceDatasetId") != DATASET_ID
    ]
    features.extend(imported)
    payload = {
        "type": "FeatureCollection",
        "name": "Verified project flight trajectories",
        "project": "Multi-project operations",
        "scope": "project_operations",
        "dataType": "actual_flight_track",
        "featureCount": len(features),
        "features": features,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {
        "datasetId": DATASET_ID,
        "featuresImported": len(imported),
        "pointsImported": sum(feature["properties"]["pointCount"] for feature in imported),
        "dates": sorted({feature["properties"]["date"] for feature in imported}),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--licences", required=True, type=Path)
    parser.add_argument("--existing", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.licences, args.existing, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
