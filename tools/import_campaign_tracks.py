"""Import survey-campaign GPS tracks that are not tied to a tracker mission.

A campaign is an acquisition programme in its own right (for example the 2025
Hetsuu Hutul UAV magnetic survey). Its sources carry no 2026 tracker mission,
so the tracker-bound importer can never emit them. This tool keeps the two
programmes apart: it never copies a tracker mission, a tracker date or a
tracker sensor onto campaign geometry.

A file is imported only when a campaign explicitly declares it, its own header
carries latitude/longitude, and its coordinates fall inside the project licence
extent. Everything else is recorded in the audit with a reason code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coordinate_sources as cs  # noqa: E402

PROJECTS = {
    "hetsuu-hutul": {"project": "Hetsuu hutul", "area": "Хэцүү хөтөл", "licence": "XV-022905"},
    "artsat": {"project": "Artsat", "area": "Арцат", "licence": "XV-021395"},
    "buduunkhad": {"project": "Buduunkhad", "area": "Бүдүүн хад", "licence": "XV-023222"},
}
DATA_TYPE = "survey_campaign_track"
DEFAULT_TIME_GAP_SECONDS = 30.0
DEFAULT_DISTANCE_GAP_M = 250.0
DEFAULT_MIN_SEGMENT_POINTS = 20
DEFAULT_SIMPLIFY_TOLERANCE_M = 2.5
# Two sources are the same survey when nearly all of one lands on ground the
# other already covers. Cells are ~11 m, so this compares real geometry.
DUPLICATE_CELL_DEGREES = 1e-4
DUPLICATE_CONTAINMENT = 0.9


def iter_coordinate_pairs(value):
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        yield float(value[0]), float(value[1])
    elif isinstance(value, list):
        for item in value:
            yield from iter_coordinate_pairs(item)


def licence_extent(licences_path: Path, project_key: str) -> tuple[float, float, float, float]:
    data = json.loads(licences_path.read_text(encoding="utf-8"))
    wanted = PROJECTS[project_key]["licence"]
    for feature in data.get("features", []):
        if feature.get("properties", {}).get("LICENSE") != wanted:
            continue
        points = list(iter_coordinate_pairs((feature.get("geometry") or {}).get("coordinates")))
        if points:
            return (
                min(point[0] for point in points), min(point[1] for point in points),
                max(point[0] for point in points), max(point[1] for point in points),
            )
    raise ValueError(f"No licence extent for {project_key}")


def inside(point: cs.TrackPoint, extent, margin: float) -> bool:
    west, south, east, north = extent
    return west - margin <= point.lon <= east + margin and south - margin <= point.lat <= north + margin


def cell_signature(points: list[cs.TrackPoint]) -> set[tuple[int, int]]:
    return {
        (int(point.lon / DUPLICATE_CELL_DEGREES), int(point.lat / DUPLICATE_CELL_DEGREES))
        for point in points
    }


def altitude_stats(values: list[float], prefix: str, column: str, reference: str) -> dict:
    finite = [value for value in values if value is not None and -1000 <= value <= 10000]
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


def load_campaigns(path: Path, project_key: str) -> dict[str, dict]:
    config = json.loads(path.read_text(encoding="utf-8"))
    return {
        campaign["id"]: campaign
        for campaign in config.get("campaigns", [])
        if campaign.get("projectKey") == project_key
    }


def load_mapping(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("sources", data) if isinstance(data, dict) else data


def canonical_rank(entry: dict, campaign: dict) -> tuple[int, int]:
    """Lower sorts first. Per-acquisition files outrank merged exports."""
    name = entry.get("file", "")
    patterns = campaign.get("acquisitionFilePatterns", [])
    is_acquisition = any(re.search(pattern, name, re.IGNORECASE) for pattern in patterns)
    return (0 if is_acquisition else 1, 0)


def build(args) -> dict:
    project = PROJECTS[args.project]
    extent = licence_extent(args.licences, args.project)
    campaigns = load_campaigns(args.campaigns, args.project)
    entries = load_mapping(args.mapping)
    audit: list[dict] = []
    features: list[dict] = []
    accepted_signatures: list[tuple[str, set[tuple[int, int]]]] = []
    seen_checksums: dict[str, str] = {}

    def record(entry, code, detail=None, extra=None):
        item = {"file": entry.get("file"), "campaignId": entry.get("campaignId"), "reason": code}
        if detail:
            item["detail"] = detail
        if extra:
            item.update(extra)
        audit.append(item)

    ordered = sorted(
        entries,
        key=lambda entry: (
            entry.get("campaignId") or "",
            canonical_rank(entry, campaigns.get(entry.get("campaignId"), {})),
            entry.get("file", ""),
        ),
    )

    for entry in ordered:
        campaign = campaigns.get(entry.get("campaignId"))
        if not campaign:
            record(entry, cs.MISSION_UNMATCHED, "no campaign declares this source")
            continue
        path = args.source_dir / entry["file"]
        if not path.exists():
            record(entry, cs.MISSING_SOURCE, "declared source file is not present locally")
            continue
        payload = path.read_bytes()
        checksum = entry.get("md5Checksum") or hashlib.md5(payload).hexdigest()
        if checksum in seen_checksums:
            record(entry, cs.DUPLICATE_SOURCE, f"identical content to {seen_checksums[checksum]}",
                   {"canonicalFile": seen_checksums[checksum], "basis": "checksum"})
            continue

        if path.suffix.lower() not in {".csv", ".txt"}:
            record(entry, cs.UNSUPPORTED_FLIGHT_LOG, f"{path.suffix} is not a delimited coordinate table")
            continue
        text = payload.decode("utf-8-sig", errors="replace")
        try:
            table = cs.parse_table(text)
        except ValueError as error:
            record(entry, cs.NOT_A_TRAJECTORY, str(error))
            continue
        if not table.points:
            record(entry, cs.INVALID_COORDINATES,
                   f"no usable coordinate rows out of {table.row_count}",
                   {"invalidRows": table.invalid_coordinate_rows})
            continue

        margin = float(campaign.get("licenceMarginDegrees", 0.02))
        inside_points = [point for point in table.points if inside(point, extent, margin)]
        outside = len(table.points) - len(inside_points)
        if not inside_points:
            record(entry, cs.WRONG_PROJECT_LOCATION,
                   f"all {len(table.points)} points fall outside the {project['licence']} extent")
            continue
        if outside and outside / len(table.points) > float(campaign.get("maxOutsideFraction", 0.5)):
            record(entry, cs.WRONG_PROJECT_LOCATION,
                   f"{outside}/{len(table.points)} points fall outside the licence extent")
            continue

        cleaned = cs.deduplicate_consecutive(inside_points)
        signature = cell_signature(cleaned)
        # A merged export re-states the ground of several per-acquisition files,
        # so containment is measured against everything accepted so far.
        covered = set().union(*(item[1] for item in accepted_signatures)) if accepted_signatures else set()
        overlap = len(signature & covered)
        if signature and overlap / len(signature) >= DUPLICATE_CONTAINMENT:
            contributors = sorted(
                name for name, canonical in accepted_signatures if signature & canonical
            )
            record(entry, cs.DUPLICATE_SOURCE,
                   f"{overlap / len(signature):.0%} of its ground is already covered by "
                   f"{', '.join(contributors)}",
                   {"canonicalFiles": contributors, "basis": "coordinate-overlap"})
            continue

        segments = cs.split_segments(
            cleaned,
            time_gap_seconds=float(campaign.get("timeGapSeconds", DEFAULT_TIME_GAP_SECONDS)),
            distance_gap_m=float(campaign.get("distanceGapM", DEFAULT_DISTANCE_GAP_M)),
            min_points=int(campaign.get("minSegmentPoints", DEFAULT_MIN_SEGMENT_POINTS)),
        )
        if not segments:
            record(entry, cs.NOT_A_TRAJECTORY,
                   f"no segment reached {campaign.get('minSegmentPoints', DEFAULT_MIN_SEGMENT_POINTS)} points")
            continue

        has_time = any(point.timestamp for point in cleaned)
        qa_flags = []
        if not has_time:
            qa_flags.append(cs.AMBIGUOUS_DATE_TIME)
        if table.ambiguous_time_rows:
            qa_flags.append(cs.AMBIGUOUS_DATE_TIME)
        if table.nmea_inconsistent_rows:
            # GGA and RMC disagree within a row: the file was stitched from
            # different samples and its timing cannot be trusted.
            qa_flags.append("NMEA_TIME_INCONSISTENT")
        if outside:
            qa_flags.append("POINTS_OUTSIDE_LICENCE")
        if table.invalid_coordinate_rows:
            qa_flags.append("INVALID_ROWS_DROPPED")
        qa_flags = sorted(set(qa_flags))
        # Without a trustworthy date/time the record is a source under review,
        # not a confirmed flight trajectory.
        status = "confirmed_source"
        if cs.AMBIGUOUS_DATE_TIME in qa_flags or "NMEA_TIME_INCONSISTENT" in qa_flags:
            status = "review_required"

        tolerance = float(campaign.get("simplifyToleranceM", DEFAULT_SIMPLIFY_TOLERANCE_M))
        stem = Path(entry["file"]).stem
        for index, segment in enumerate(segments, start=1):
            simplified = cs.simplify(segment, tolerance)
            interval = cs.median_interval_seconds(segment)
            start = segment[0].timestamp
            end = segment[-1].timestamp
            properties = {
                "project": project["project"],
                "projectKey": args.project,
                "area": project["area"],
                "licence": project["licence"],
                "campaignId": campaign["id"],
                "campaignLabel": campaign.get("label", campaign["id"]),
                "campaignYear": campaign.get("campaignYear"),
                "sensor": campaign.get("sensor", "Unknown"),
                "sensorVerified": bool(campaign.get("sensorVerified", False)),
                "gnssSource": campaign.get("gnssSource", "unverified"),
                "dataType": DATA_TYPE,
                "plannedActual": "actual",
                "contextOnly": True,
                "trajectoryAvailable": True,
                "status": status,
                "qaStatus": "review_required" if qa_flags else "clean",
                "qaFlags": qa_flags,
                "acquisitionId": f"{stem}#{index}",
                "segmentIndex": index,
                "segmentCount": len(segments),
                "pointCount": len(segment),
                "simplifiedPointCount": len(simplified),
                "pointsOutsideLicence": outside if index == 1 else 0,
                "startTime": start.isoformat() if start else None,
                "endTime": end.isoformat() if end else None,
                # An NMEA RMC timestamp is UTC by definition; anything read
                # from a plain date/time column keeps the declared (or
                # unverified) zone rather than being shifted.
                "timeZone": "UTC (NMEA RMC)" if table.timestamp_provenance == "embedded-nmea-rmc"
                else campaign.get("timeZone", "unverified"),
                "samplingIntervalSeconds": round(interval, 4) if interval else None,
                "timestampProvenance": table.timestamp_provenance,
                "latitudeColumn": table.columns.lat_name,
                "longitudeColumn": table.columns.lon_name,
                "sourceFile": entry["file"],
                "sourceKind": campaign.get("sourceKind", "Survey GNSS coordinate table"),
                "sourceCrs": "EPSG:4326",
                "displayCrs": "EPSG:4326",
                "crsVerified": True,
                "coverageStatus": campaign.get("coverageStatus", "not_calculated"),
                "coverageNote": campaign.get(
                    "coverageNote",
                    "Swath/line spacing is not verified for this campaign, so no surveyed area is reported.",
                ),
            }
            if entry.get("sourceUrl"):
                properties["sourceUrl"] = entry["sourceUrl"]
            properties.update(altitude_stats(
                [point.absolute_alt for point in segment],
                "sourceAltitude", table.columns.absolute_alt_name or "n/a",
                "source-provided GNSS/absolute altitude",
            ))
            properties.update(altitude_stats(
                [point.relative_alt for point in segment],
                "flightHeight", table.columns.relative_alt_name or "n/a",
                "source-provided relative height (not terrain-derived AGL)",
            ))
            features.append({
                "type": "Feature",
                "id": f"{args.project}-{campaign['id']}-{cs.normalise(stem)}-{index:03d}",
                "properties": properties,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[round(point.lon, 8), round(point.lat, 8)] for point in simplified],
                },
            })
        accepted_signatures.append((entry["file"], signature))
        seen_checksums[checksum] = entry["file"]
        record(entry, cs.IMPORTED, f"{len(segments)} segment(s)", {
            "segments": len(segments),
            "pointsUsed": len(cleaned),
            "pointsOutsideLicence": outside,
            "qaFlags": qa_flags,
        })

    # Anything already published that this run did not rebuild is carried over,
    # so a run with no sources can never blank an existing campaign.
    rebuilt = {(args.project, feature["properties"]["campaignId"]) for feature in features}
    if args.existing and args.existing.exists():
        previous = json.loads(args.existing.read_text(encoding="utf-8"))
        for feature in previous.get("features", []):
            props = feature.get("properties", {})
            if (props.get("projectKey"), props.get("campaignId")) not in rebuilt:
                features.append(feature)
    features.sort(key=lambda feature: feature["id"])
    output = {
        "type": "FeatureCollection",
        "name": "Survey campaign GNSS tracks",
        "project": "Multi-project operations",
        "scope": "project_operations",
        "dataType": DATA_TYPE,
        "featureCount": len(features),
        "features": features,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    counts: dict[str, int] = {}
    for item in audit:
        counts[item["reason"]] = counts.get(item["reason"], 0) + 1
    report = {
        "project": args.project,
        "declaredSources": len(entries),
        "importedSources": counts.get(cs.IMPORTED, 0),
        "segments": len(features),
        "reasonCounts": dict(sorted(counts.items())),
        "audit": audit,
    }
    if args.audit:
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.audit.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--project", choices=sorted(PROJECTS), default="hetsuu-hutul")
    parser.add_argument("--campaigns", required=True, type=Path)
    parser.add_argument("--licences", required=True, type=Path)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--existing", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    report = build(args)
    summary = {key: value for key, value in report.items() if key != "audit"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
