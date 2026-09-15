"""Export a polygon Shapefile from a ZIP archive to browser-ready GeoJSON."""

from __future__ import annotations

import argparse
import json
import struct
import zipfile
from pathlib import Path


def read_dbf(path: Path) -> list[dict[str, str]]:
    data = path.read_bytes()
    count, header_length, record_length = struct.unpack_from("<IHH", data, 4)
    fields = []
    offset = 32
    while data[offset] != 0x0D:
        name = data[offset:offset + 11].split(b"\0", 1)[0].decode("ascii")
        fields.append((name, data[offset + 16]))
        offset += 32
    records = []
    for index in range(count):
        row = data[header_length + index * record_length:header_length + (index + 1) * record_length]
        values = {}
        position = 1
        for name, length in fields:
            raw = row[position:position + length].rstrip()
            try:
                values[name] = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                values[name] = raw.decode("cp1251", errors="replace").strip()
            position += length
        records.append(values)
    return records


def read_polygons(path: Path) -> list[list[list[list[float]]]]:
    data = path.read_bytes()
    shape_type = struct.unpack_from("<i", data, 32)[0]
    if shape_type != 5:
        raise ValueError(f"Unsupported Shapefile type {shape_type}; expected Polygon (5)")
    offset = 100
    polygons = []
    while offset + 8 <= len(data):
        _, content_words = struct.unpack_from(">ii", data, offset)
        offset += 8
        end = offset + content_words * 2
        record_type = struct.unpack_from("<i", data, offset)[0]
        if record_type != 5:
            raise ValueError(f"Unsupported record type {record_type}")
        part_count, point_count = struct.unpack_from("<ii", data, offset + 36)
        parts_offset = offset + 44
        points_offset = parts_offset + part_count * 4
        parts = list(struct.unpack_from(f"<{part_count}i", data, parts_offset))
        parts.append(point_count)
        rings = []
        for start, finish in zip(parts, parts[1:]):
            points = [
                list(struct.unpack_from("<dd", data, points_offset + point * 16))
                for point in range(start, finish)
            ]
            rings.append(points)
        polygons.append(rings)
        offset = end
    return polygons


def transform_point(point: list[float], utm: bool) -> list[float]:
    if not utm:
        return point
    from export_gpkg import utm_to_wgs84
    return utm_to_wgs84(point[0], point[1], 49, True)


def export(source: Path, output: Path, utm: bool = False) -> None:
    with zipfile.ZipFile(source) as archive:
        names = {Path(name).suffix.lower(): name for name in archive.namelist()}
        with archive.open(names[".shp"]) as shp, archive.open(names[".dbf"]) as dbf:
            temp_shp = output.with_suffix(".tmp.shp")
            temp_dbf = output.with_suffix(".tmp.dbf")
            temp_shp.write_bytes(shp.read())
            temp_dbf.write_bytes(dbf.read())
    try:
        polygons = read_polygons(temp_shp)
        records = read_dbf(temp_dbf)
    finally:
        temp_shp.unlink(missing_ok=True)
        temp_dbf.unlink(missing_ok=True)

    features = []
    for index, (rings, properties) in enumerate(zip(polygons, records), start=1):
        properties = {key: value for key, value in properties.items() if value}
        for key, value in properties.items():
            if value == "Buduunhhad":
                properties[key] = "Buduunkhad"
        properties["layer"] = "Additional_Licenses"
        coordinates = [
            [transform_point(point, utm) for point in ring]
            for ring in rings
        ]
        features.append({
            "type": "Feature",
            "id": f"license:{index}",
            "properties": properties,
            "geometry": {"type": "Polygon", "coordinates": coordinates},
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": source.stem,
        "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Exported {len(features)} license feature(s) to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--utm-zone-49n", action="store_true")
    args = parser.parse_args()
    export(args.source, args.output, args.utm_zone_49n)
