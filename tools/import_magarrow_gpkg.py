#!/usr/bin/env python3
"""Import processed MagArrow flight paths from a GeoPackage.

The processed campaign package contains one ``flight_paths`` row and many
``mag_points`` rows per acquisition.  The latter already expose WGS 84
longitude/latitude columns, so this importer needs only Python's standard
library and does not depend on GDAL/OGR or pyproj.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from export_magdata_trajectories import rdp
from sync_drive_sources import magarrow_project_for_licence, project_magarrow_feature


def iso_utc(value: str | None) -> str | None:
    value = (value or "").strip()
    return f"{value.replace(' ', 'T')}Z" if value else None


def load_campaign_features(gpkg_path: Path, project: dict) -> list[dict]:
    digest = hashlib.sha256(gpkg_path.read_bytes()).hexdigest()
    connection = sqlite3.connect(gpkg_path)
    connection.row_factory = sqlite3.Row
    try:
        summaries = {
            row["flight"]: row
            for row in connection.execute(
                """
                SELECT flight, flight_id, acqu, src_file, date_local,
                       start_local, end_local, n_points
                FROM flight_summary
                ORDER BY date_local, acqu
                """
            )
        }
        points: dict[str, list[list[float]]] = defaultdict(list)
        times: dict[str, list[str]] = defaultdict(list)
        for row in connection.execute(
            """
            SELECT flight, time_utc, lon, lat
            FROM mag_points
            WHERE lon BETWEEN -180 AND 180 AND lat BETWEEN -90 AND 90
            ORDER BY flight, time_utc, fid
            """
        ):
            points[row["flight"]].append([float(row["lon"]), float(row["lat"])])
            if row["time_utc"]:
                times[row["flight"]].append(row["time_utc"])
    finally:
        connection.close()

    features = []
    for flight, summary in summaries.items():
        coordinates = points.get(flight, [])
        if len(coordinates) < 2:
            continue
        acquisition = f"SRVY0-ACQU{summary['acqu']}"
        day = summary["date_local"]
        source_hash = hashlib.sha256(
            f"{digest}:{summary['flight_id']}".encode("utf-8")
        ).hexdigest()[:24]
        flight_times = times.get(flight, [])
        base = {
            "type": "Feature",
            "properties": {
                "acquisition": acquisition,
                "date": day,
                "startTime": iso_utc(flight_times[0]) if flight_times else None,
                "endTime": iso_utc(flight_times[-1]) if flight_times else None,
                "sourceFile": summary["src_file"] or gpkg_path.name,
                "pointCount": int(summary["n_points"] or len(coordinates)),
                "nmeaSource": "processed MagArrow GeoPackage (WGS 84 lon/lat)",
                "sourceFingerprint": source_hash,
                "sourceFingerprints": [source_hash],
                "sourceIdHash": source_hash,
                "sourceIdHashes": [source_hash],
            },
            "geometry": {"type": "LineString", "coordinates": rdp(coordinates, 2.5)},
        }
        published = project_magarrow_feature(base, project)
        published["properties"]["sourceKind"] = "Processed MagArrow GeoPackage"
        published["properties"]["sourceDataset"] = gpkg_path.name
        features.append(published)
    return features


def merge_features(output_path: Path, features: list[dict]) -> dict:
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    replacement_ids = {feature["id"] for feature in features}
    payload["features"] = [
        feature for feature in payload.get("features", [])
        if feature.get("id") not in replacement_ids
    ] + features
    payload["features"].sort(key=lambda feature: (
        feature.get("properties", {}).get("projectKey", ""),
        feature.get("properties", {}).get("date", ""),
        feature.get("id", ""),
    ))
    payload["featureCount"] = len(payload["features"])
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return payload


def import_gpkg(gpkg_path: Path, repo: Path, project_key: str = "artsat") -> dict:
    config = json.loads((repo / "drive-sync-sources.json").read_text(encoding="utf-8"))
    configured = next(item for item in config["projects"] if item["key"] == project_key)
    project = magarrow_project_for_licence({
        "code": configured["licence"],
        "name": project_key,
        "label": project_key,
    }, config["projects"])
    features = load_campaign_features(gpkg_path, project)
    output = repo / "dist" / "data" / "context" / "project-flight-tracks.geojson"
    merge_features(output, features)
    dates = sorted({feature["properties"]["date"] for feature in features})
    return {
        "projectKey": project_key,
        "importedCount": len(features),
        "dateFrom": dates[0] if dates else None,
        "dateTo": dates[-1] if dates else None,
        "sourceFile": gpkg_path.name,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpkg", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--project-key", default="artsat")
    args = parser.parse_args()
    print(json.dumps(
        import_gpkg(args.gpkg.resolve(), args.repo.resolve(), args.project_key),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
