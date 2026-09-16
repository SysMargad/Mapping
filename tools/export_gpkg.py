"""Export GeoPackage feature layers to browser-ready GeoJSON.

This intentionally uses only Python's standard library so it can run on a clean
Windows machine without installing GDAL. It supports point, line and polygon
data stored in WGS 84 or a WGS 84 UTM zone (EPSG:326xx / EPSG:327xx).
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import struct
from pathlib import Path


MAGARROW_LAYER_NAMES = {
    "P1_Main_50m": "MagArrow_Main_50m",
    "P1_Main_100m_AZ88": "MagArrow_Main_100m_AZ88",
    "P1_Tie_300m": "MagArrow_Tie_300m",
    "P1_Tie_200m_AZ178": "MagArrow_Tie_200m_AZ178",
}


def read_uint(data: bytes, offset: int, size: int, little: bool) -> tuple[int, int]:
    fmt = ("<" if little else ">") + ("I" if size == 4 else "Q")
    return struct.unpack_from(fmt, data, offset)[0], offset + size


def read_double(data: bytes, offset: int, little: bool) -> tuple[float, int]:
    return struct.unpack_from(("<" if little else ">") + "d", data, offset)[0], offset + 8


def read_point(data: bytes, offset: int, little: bool, dims: int) -> tuple[list[float], int]:
    values = []
    for _ in range(dims):
        value, offset = read_double(data, offset, little)
        values.append(value)
    return values[:2], offset


def read_wkb(data: bytes, offset: int = 0) -> tuple[dict, int]:
    little = data[offset] == 1
    offset += 1
    raw_type, offset = read_uint(data, offset, 4, little)

    # Handle OGC 1000/2000/3000 dimensions and EWKB Z/M flags.
    base_type = raw_type & 0xFF
    dims = 2
    if raw_type >= 3000 and raw_type < 4000:
        base_type, dims = raw_type - 3000, 4
    elif raw_type >= 2000 and raw_type < 3000:
        base_type, dims = raw_type - 2000, 3
    elif raw_type >= 1000 and raw_type < 2000:
        base_type, dims = raw_type - 1000, 3
    elif raw_type & 0x80000000:
        dims += 1
    if raw_type & 0x40000000:
        dims += 1

    if base_type == 1:  # Point
        point, offset = read_point(data, offset, little, dims)
        return {"type": "Point", "coordinates": point}, offset

    if base_type == 2:  # LineString
        point_count, offset = read_uint(data, offset, 4, little)
        points = []
        for _ in range(point_count):
            point, offset = read_point(data, offset, little, dims)
            points.append(point)
        return {"type": "LineString", "coordinates": points}, offset

    if base_type == 3:  # Polygon
        ring_count, offset = read_uint(data, offset, 4, little)
        rings = []
        for _ in range(ring_count):
            point_count, offset = read_uint(data, offset, 4, little)
            ring = []
            for _ in range(point_count):
                point, offset = read_point(data, offset, little, dims)
                ring.append(point)
            rings.append(ring)
        return {"type": "Polygon", "coordinates": rings}, offset

    collection_types = {
        4: ("MultiPoint", "Point"),
        5: ("MultiLineString", "LineString"),
        6: ("MultiPolygon", "Polygon"),
    }
    if base_type in collection_types:
        output_type, child_type = collection_types[base_type]
        geometry_count, offset = read_uint(data, offset, 4, little)
        coordinates = []
        for _ in range(geometry_count):
            child, offset = read_wkb(data, offset)
            if child["type"] != child_type:
                raise ValueError(f"{output_type} contained a {child['type']} geometry")
            coordinates.append(child["coordinates"])
        return {"type": output_type, "coordinates": coordinates}, offset

    if base_type == 7:  # GeometryCollection
        geometry_count, offset = read_uint(data, offset, 4, little)
        geometries = []
        for _ in range(geometry_count):
            child, offset = read_wkb(data, offset)
            geometries.append(child)
        return {"type": "GeometryCollection", "geometries": geometries}, offset

    raise ValueError(f"Unsupported WKB geometry type: {base_type}")


def unpack_gpkg_geometry(blob: bytes) -> tuple[int, dict]:
    if blob[:2] != b"GP":
        raise ValueError("Not a GeoPackage geometry blob")
    flags = blob[3]
    little = bool(flags & 1)
    srs_id = struct.unpack_from("<i" if little else ">i", blob, 4)[0]
    envelope_code = (flags >> 1) & 7
    envelope_doubles = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}.get(envelope_code)
    if envelope_doubles is None:
        raise ValueError(f"Unsupported GeoPackage envelope code: {envelope_code}")
    geometry, _ = read_wkb(blob, 8 + envelope_doubles * 8)
    return srs_id, geometry


def utm_to_wgs84(easting: float, northing: float, zone: int, northern: bool) -> list[float]:
    # USGS Bulletin 1532 inverse Transverse Mercator equations.
    a = 6378137.0
    ecc_sq = 0.0066943799901413165
    ecc_prime_sq = ecc_sq / (1 - ecc_sq)
    k0 = 0.9996
    x = easting - 500000.0
    y = northing if northern else northing - 10000000.0
    m = y / k0
    mu = m / (a * (1 - ecc_sq / 4 - 3 * ecc_sq**2 / 64 - 5 * ecc_sq**3 / 256))
    e1 = (1 - math.sqrt(1 - ecc_sq)) / (1 + math.sqrt(1 - ecc_sq))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    n1 = a / math.sqrt(1 - ecc_sq * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = ecc_prime_sq * math.cos(phi1) ** 2
    r1 = a * (1 - ecc_sq) / (1 - ecc_sq * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ecc_prime_sq) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ecc_prime_sq - 3 * c1**2) * d**6 / 720
    )
    lon = (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ecc_prime_sq + 24 * t1**2) * d**5 / 120
    ) / math.cos(phi1)
    lon_origin = math.radians((zone - 1) * 6 - 180 + 3)
    return [round(math.degrees(lon_origin + lon), 8), round(math.degrees(lat), 8)]


def transform_geometry(geometry: dict, srs_id: int, assigned_epsg: int | None = None) -> dict:
    if srs_id in (4326, 4979):
        return geometry
    if srs_id == 99999:
        if assigned_epsg is None:
            raise ValueError(
                "GeoPackage uses unverified SRS 99999. Pass --assign-epsg only after "
                "explicitly reviewing the coordinate system."
            )
        srs_id = assigned_epsg
    if 32601 <= srs_id <= 32660:
        zone, northern = srs_id - 32600, True
    elif 32701 <= srs_id <= 32760:
        zone, northern = srs_id - 32700, False
    else:
        raise ValueError(f"Unsupported CRS EPSG:{srs_id}; expected WGS 84 or WGS 84 / UTM")

    def point(value):
        if value and isinstance(value[0], (int, float)):
            return utm_to_wgs84(value[0], value[1], zone, northern)
        return [point(item) for item in value]

    if geometry["type"] == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "geometries": [transform_geometry(item, srs_id, assigned_epsg) for item in geometry["geometries"]],
        }
    return {"type": geometry["type"], "coordinates": point(geometry["coordinates"])}


def ring_area(ring: list[list[float]]) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def geometry_area(geometry: dict) -> float | None:
    if geometry["type"] not in ("Polygon", "MultiPolygon"):
        return None
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return sum(ring_area(poly[0]) - sum(ring_area(hole) for hole in poly[1:]) for poly in polygons)


def export(source: Path, output: Path, layer: str | None, profile: str, assigned_epsg: int | None) -> None:
    uri = f"file:{source.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        layers = connection.execute(
            "SELECT c.table_name, g.column_name, g.geometry_type_name, g.srs_id "
            "FROM gpkg_contents c JOIN gpkg_geometry_columns g ON c.table_name = g.table_name "
            "WHERE c.data_type = 'features'"
        ).fetchall()
        selected_layers = [row for row in layers if row["table_name"] == layer] if layer else list(layers)
        if not selected_layers:
            names = ", ".join(row["table_name"] for row in layers) or "none"
            raise ValueError(f"Layer not found. Available layers: {names}")

        features = []
        layer_summary = []
        for selected in selected_layers:
            table = selected["table_name"].replace('"', '""')
            geom_column = selected["column_name"]
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            property_columns = [column for column in columns if column != geom_column]
            select_columns = ", ".join(f'"{column.replace(chr(34), chr(34) * 2)}"' for column in columns)
            layer_count = 0
            for row in connection.execute(f'SELECT {select_columns} FROM "{table}"'):
                if row[geom_column] is None:
                    continue
                srs_id, source_geometry = unpack_gpkg_geometry(row[geom_column])
                properties = {column: row[column] for column in property_columns}
                source_layer = selected["table_name"]
                output_layer = MAGARROW_LAYER_NAMES.get(source_layer, source_layer)
                properties["layer"] = output_layer
                properties["project"] = "Nergui Undur"
                properties["display_epsg"] = 4326
                if profile == "magarrow-plan":
                    properties.update({
                        "area": "Heseg Uul hoid",
                        "sensor": "MagArrow",
                        "plannedActual": "planned",
                        "status": "confirmed",
                        "source_epsg": srs_id,
                        "crs_verified": srs_id == 32649,
                    })
                    if output_layer.startswith("MagArrow_Main"):
                        properties["dataType"] = "planned_main_line"
                    elif output_layer.startswith("MagArrow_Tie"):
                        properties["dataType"] = "planned_tie_line"
                    elif output_layer == "Survey_Area":
                        properties["dataType"] = "planned_survey_boundary"
                    else:
                        properties["dataType"] = "planned_support"
                else:
                    properties["source_epsg"] = srs_id if srs_id != 99999 else None
                    properties["crs_verified"] = srs_id != 99999
                area = geometry_area(source_geometry)
                if area is not None:
                    properties["area_m2"] = round(area, 2)
                features.append(
                    {
                        "type": "Feature",
                        "id": f"{output_layer}:{properties.get('fid', layer_count + 1)}",
                        "properties": properties,
                        "geometry": transform_geometry(source_geometry, srs_id, assigned_epsg),
                    }
                )
                layer_count += 1
            layer_summary.append(
                {
                    "name": MAGARROW_LAYER_NAMES.get(selected["table_name"], selected["table_name"]),
                    "geometry_type": selected["geometry_type_name"],
                    "source_epsg": selected["srs_id"],
                    "feature_count": layer_count,
                }
            )
    finally:
        connection.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": source.stem,
                "layers": layer_summary,
                "features": features,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print(f"Exported {len(features)} feature(s) from {len(selected_layers)} layer(s) to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--layer")
    parser.add_argument("--profile", choices=("generic", "magarrow-plan"), default="generic")
    parser.add_argument("--assign-epsg", type=int)
    args = parser.parse_args()
    export(args.source, args.output, args.layer, args.profile, args.assign_epsg)
