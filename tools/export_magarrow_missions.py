"""Export Nergui Undur MagArrow DJI WPMZ/KMZ mission plans to GeoJSON.

The LineString in ``template.kml`` is a planned DJI mission route.  It is not
an actual flown trajectory.  This exporter deliberately writes explicit
provenance and planned/actual metadata so the two concepts cannot be mixed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def text(element, local_name: str) -> str | None:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1] == local_name:
            return (child.text or "").strip()
    return None


def coordinates(value: str) -> list[list[float]]:
    points = []
    for item in re.split(r"\s+", value.strip()):
        values = item.split(",")
        if len(values) >= 2:
            points.append([float(values[0]), float(values[1])])
    return points


def read_archive(source: Path, label: str) -> list[dict]:
    with zipfile.ZipFile(source) as archive:
        template_name = next(name for name in archive.namelist() if name.endswith("template.kml"))
        root = ElementTree.fromstring(archive.read(template_name))
    create_time = text(root, "createTime")
    update_time = text(root, "updateTime")
    timestamp = int(create_time or update_time or 0) / 1000
    date = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime("%Y-%m-%d") if timestamp else ""
    features = []
    for line in root.iter():
        if line.tag.rsplit("}", 1)[-1] != "LineString":
            continue
        value = next((child.text for child in line if child.tag.rsplit("}", 1)[-1] == "coordinates"), "")
        points = coordinates(value or "")
        if len(points) < 2:
            continue
        features.append({
            "type": "Feature",
            "id": f"{label}:{len(features) + 1}",
            "properties": {
                "layer": "MagArrow_Mission",
                "project": "Nergui Undur",
                "area": "Heseg Uul hoid",
                "sensor": "MagArrow",
                "dataType": "mission_plan",
                "plannedActual": "planned",
                "status": "confirmed",
                "mission_id": label,
                "plan": label,
                "date": date,
                "sourceFile": source.name,
                "sourceUrl": "https://drive.google.com/drive/folders/1n5C1QrH1ZQfuFOOpDfpGk_4hgrwBdlXS",
                "sourceCrs": "EPSG:4326",
                "displayCrs": "EPSG:4326",
                "crsVerified": True,
            },
            "geometry": {"type": "LineString", "coordinates": points},
        })
    return features


def export(source_dir: Path, output: Path) -> None:
    features = []
    plans = []
    for source in sorted(source_dir.glob("L*.zip")):
        label = source.stem.upper()
        plan_features = read_archive(source, label)
        if not plan_features:
            continue
        date = plan_features[0]["properties"]["date"]
        features.extend(plan_features)
        plans.append({
            "label": label,
            "mission_id": label,
            "date": date,
            "sourceFile": source.name,
            "feature_count": len(plan_features),
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "MagArrow DJI Mission Plans",
        "project": "Nergui Undur",
        "sensor": "MagArrow",
        "dataType": "mission_plan",
        "plannedActual": "planned",
        "plans": plans,
        "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Exported {len(features)} MagArrow mission-plan feature(s) from {len(plans)} plans to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export(args.source_dir, args.output)
