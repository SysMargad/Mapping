"""Shared detection and parsing for coordinate-bearing survey source files.

Used by the Drive sync (to decide whether a candidate file is worth
downloading) and by the campaign importer (to turn a verified file into
trajectory segments). Nothing here infers content from a file name: a table is
accepted only when its own header carries usable latitude/longitude columns and
its rows form a time-ordered sequence.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, time as time_cls

# Audit reason codes. Every source a pipeline stage drops must carry one.
IMPORTED = "IMPORTED"
MISSING_SOURCE = "MISSING_SOURCE"
ACCESS_DENIED = "ACCESS_DENIED"
UNSUPPORTED_FLIGHT_LOG = "UNSUPPORTED_FLIGHT_LOG"
INVALID_COORDINATES = "INVALID_COORDINATES"
AMBIGUOUS_DATE_TIME = "AMBIGUOUS_DATE_TIME"
DATE_MISMATCH = "DATE_MISMATCH"
MISSION_UNMATCHED = "MISSION_UNMATCHED"
AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
DUPLICATE_SOURCE = "DUPLICATE_SOURCE"
WRONG_PROJECT_LOCATION = "WRONG_PROJECT_LOCATION"
NOT_A_TRAJECTORY = "NOT_A_TRAJECTORY"

REASON_CODES = frozenset({
    IMPORTED, MISSING_SOURCE, ACCESS_DENIED, UNSUPPORTED_FLIGHT_LOG,
    INVALID_COORDINATES, AMBIGUOUS_DATE_TIME, DATE_MISMATCH, MISSION_UNMATCHED,
    AMBIGUOUS_MATCH, DUPLICATE_SOURCE, WRONG_PROJECT_LOCATION, NOT_A_TRAJECTORY,
})

LAT_NAMES = ("latitude", "lat", "gpslatitude", "gpslat", "latdeg", "latitudedeg", "ycoord", "wgs84lat")
LON_NAMES = ("longitude", "lon", "lng", "long", "gpslongitude", "gpslon", "londeg", "longitudedeg", "xcoord", "wgs84lon")
DATE_NAMES = ("date", "gpsdate", "utcdate", "surveydate", "acquisitiondate")
TIME_NAMES = ("time", "gpstime", "utctime", "timestamp", "datetime", "gpsdatetime", "utc")
NMEA_NAMES = ("ggasentence", "rmcsentence", "nmea")
ABSOLUTE_ALT_NAMES = ("altitude", "gpsaltitude", "ellipsoidalheight", "heightoverellipsoid", "elevation", "altm", "ellh")
RELATIVE_ALT_NAMES = ("relativealtitude", "heightagl", "altitudeagl", "aglheight", "flightheight", "relativeheight", "heightabovetakeoff")

# Header tokens that mark a static point table (GCP / base / sample / assay)
# rather than a flown trajectory. Such a table is never a flight track.
POINT_TABLE_MARKERS = (
    "gcp", "controlpoint", "checkpoint", "basepoint", "benchmark", "monument",
    "sampleid", "sampleno", "samplenumber", "samplecode", "samplename",
    "pointid", "pointname", "pointno", "pointnumber", "stationid", "stationname",
    "trenchid", "holeid", "boreholeid", "assay", "lithology",
)

_SEPARATORS = ",;\t|"


def normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


@dataclass(frozen=True)
class ColumnMap:
    """Zero-based indexes plus the verbatim header text that was matched."""

    lat_index: int
    lon_index: int
    lat_name: str
    lon_name: str
    date_index: int | None = None
    date_name: str = ""
    time_index: int | None = None
    time_name: str = ""
    absolute_alt_index: int | None = None
    absolute_alt_name: str = ""
    relative_alt_index: int | None = None
    relative_alt_name: str = ""


def _match_index(headers: list[str], candidates: tuple[str, ...], used: set[int]) -> tuple[int | None, str]:
    keys = [normalise(header) for header in headers]
    for candidate in candidates:
        for index, key in enumerate(keys):
            if index not in used and key == candidate:
                return index, headers[index].strip()
    for candidate in candidates:
        for index, key in enumerate(keys):
            if index not in used and key.startswith(candidate):
                return index, headers[index].strip()
    return None, ""


def detect_columns(headers: list[str]) -> ColumnMap | None:
    """Return the coordinate column map for a header row, or None."""
    used: set[int] = set()
    lat_index, lat_name = _match_index(headers, LAT_NAMES, used)
    if lat_index is None:
        return None
    used.add(lat_index)
    lon_index, lon_name = _match_index(headers, LON_NAMES, used)
    if lon_index is None:
        return None
    used.add(lon_index)
    date_index, date_name = _match_index(headers, DATE_NAMES, used)
    if date_index is not None:
        used.add(date_index)
    time_index, time_name = _match_index(headers, TIME_NAMES, used)
    if time_index is not None:
        used.add(time_index)
    absolute_index, absolute_name = _match_index(headers, ABSOLUTE_ALT_NAMES, used)
    if absolute_index is not None:
        used.add(absolute_index)
    relative_index, relative_name = _match_index(headers, RELATIVE_ALT_NAMES, used)
    return ColumnMap(
        lat_index=lat_index, lon_index=lon_index, lat_name=lat_name, lon_name=lon_name,
        date_index=date_index, date_name=date_name,
        time_index=time_index, time_name=time_name,
        absolute_alt_index=absolute_index, absolute_alt_name=absolute_name,
        relative_alt_index=relative_index, relative_alt_name=relative_name,
    )


def looks_like_point_table(headers: list[str]) -> bool:
    """True when the header names a static GCP/sample table, not a trajectory."""
    keys = [normalise(header) for header in headers]
    return any(marker in key for key in keys for marker in POINT_TABLE_MARKERS)


def sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=_SEPARATORS).delimiter
    except csv.Error:
        counts = {sep: sample.count(sep) for sep in _SEPARATORS}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def read_header(text: str) -> list[str] | None:
    """Read the first non-empty row of a delimited text sample as a header."""
    delimiter = sniff_delimiter(text[:8192])
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        if any(str(cell).strip() for cell in row):
            return [str(cell) for cell in row]
    return None


def header_has_coordinates(text: str) -> bool:
    """Cheap content probe for the Drive sync: does this sample carry lat/lon?"""
    header = read_header(text)
    if header is None or looks_like_point_table(header):
        return False
    return detect_columns(header) is not None


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")
_TIME_FORMATS = ("%H:%M:%S.%f", "%H:%M:%S", "%H%M%S.%f", "%H%M%S")
_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S",
    "%Y.%m.%d %H:%M:%S.%f", "%Y.%m.%d %H:%M:%S",
)
# "59:01.0" style values carry no hour and no date. They are a truncated clock
# reading, never a usable timestamp.
_TRUNCATED_CLOCK = re.compile(r"^\d{1,2}:\d{2}(?:\.\d+)?$")


class AmbiguousDateTime(ValueError):
    """Raised when a date/time value cannot be read without guessing."""


def parse_date_value(value: str) -> date_cls | None:
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # A day-first or month-first value such as 03/11/2025 cannot be resolved
    # without external knowledge, so it is reported rather than guessed.
    if re.fullmatch(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}", text):
        raise AmbiguousDateTime(f"ambiguous date order: {text!r}")
    return None


def parse_time_value(value: str) -> time_cls | None:
    text = str(value).strip()
    if not text:
        return None
    if _TRUNCATED_CLOCK.fullmatch(text):
        raise AmbiguousDateTime(f"truncated clock without an hour field: {text!r}")
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def parse_datetime_cell(value: str) -> datetime | None:
    text = str(value).strip().replace("Z", "")
    if not text:
        return None
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# Some merged exports have been round-tripped through Excel, which rewrites the
# clock as mm:ss.0 and throws the hour away. The embedded NMEA sentences still
# carry the true UTC instant, so they are the authoritative fallback.
# These exports store the NMEA hhmmss and ddmmyy fields without their leading
# zero (04:59:01 appears as 45901, 02/11/25 as 21125), so both are padded back
# to six digits before being read.
RMC_RE = re.compile(
    r"\$G[A-Z]{1,2}RMC,(\d{5,6})(?:\.\d+)?,([AV]),[^,]*,[NS],[^,]*,[EW],[^,]*,[^,]*,(\d{5,6})",
    re.IGNORECASE,
)
GGA_RE = re.compile(r"\$G[A-Z]{1,2}GGA,(\d{5,6})(?:\.\d+)?,", re.IGNORECASE)


def nmea_timestamp(line: str) -> tuple[datetime | None, bool]:
    """Recover a UTC timestamp from embedded NMEA. Returns (value, consistent).

    `consistent` is False when GGA and RMC disagree about the time, which marks
    a merged file whose sentences were stitched from different samples.
    """
    rmc = RMC_RE.search(line)
    if not rmc:
        return None, True
    clock = rmc.group(1).zfill(6)
    status = rmc.group(2).upper()
    ddmmyy = rmc.group(3).zfill(6)
    if status != "A":
        return None, True
    try:
        stamp = datetime.strptime(ddmmyy + clock, "%d%m%y%H%M%S")
    except ValueError:
        return None, True
    gga = GGA_RE.search(line)
    consistent = not (gga and gga.group(1).zfill(6) != clock)
    return stamp, consistent


def combine_timestamp(date_text: str, time_text: str) -> tuple[datetime | None, str]:
    """Return (timestamp, provenance). Never invents a missing date."""
    combined = parse_datetime_cell(time_text) or parse_datetime_cell(date_text)
    if combined is not None:
        return combined, "source-datetime-column"
    day = parse_date_value(date_text)
    clock = parse_time_value(time_text)
    if day is not None and clock is not None:
        return datetime.combine(day, clock), "source-date+time-columns"
    if day is not None:
        return datetime.combine(day, time_cls(0, 0)), "source-date-column-only"
    return None, "none"


# ---------------------------------------------------------------------------
# Rows -> points
# ---------------------------------------------------------------------------

@dataclass
class TrackPoint:
    lon: float
    lat: float
    timestamp: datetime | None = None
    absolute_alt: float | None = None
    relative_alt: float | None = None
    row_index: int = 0


@dataclass
class ParsedTable:
    columns: ColumnMap
    points: list[TrackPoint] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    row_count: int = 0
    invalid_coordinate_rows: int = 0
    ambiguous_time_rows: int = 0
    nmea_recovered_rows: int = 0
    nmea_inconsistent_rows: int = 0
    timestamp_provenance: str = "none"
    delimiter: str = ","
    has_nmea: bool = False


def _float(value) -> float | None:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_table(text: str) -> ParsedTable:
    """Parse a delimited coordinate table. Raises ValueError when unusable."""
    delimiter = sniff_delimiter(text[:8192])
    rows = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = None
    for row in rows:
        if any(str(cell).strip() for cell in row):
            header = [str(cell) for cell in row]
            break
    if header is None:
        raise ValueError("empty coordinate table")
    if looks_like_point_table(header):
        raise ValueError("header describes a static point table, not a trajectory")
    columns = detect_columns(header)
    if columns is None:
        raise ValueError("no latitude/longitude columns in header")

    result = ParsedTable(columns=columns, header=header, delimiter=delimiter)
    result.has_nmea = any(
        any(name in normalise(cell) for name in NMEA_NAMES) for cell in header
    )
    provenances: set[str] = set()
    for row_index, row in enumerate(rows, start=1):
        if not any(str(cell).strip() for cell in row):
            continue
        result.row_count += 1
        lat = _float(row[columns.lat_index]) if len(row) > columns.lat_index else None
        lon = _float(row[columns.lon_index]) if len(row) > columns.lon_index else None
        if lat is None or lon is None or (lat == 0 and lon == 0):
            result.invalid_coordinate_rows += 1
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            # Projected (for example UTM) easting/northing must not be read as
            # degrees. The row is rejected rather than silently reinterpreted.
            result.invalid_coordinate_rows += 1
            continue
        timestamp = None
        row_ambiguous = False
        if columns.date_index is not None or columns.time_index is not None:
            date_text = row[columns.date_index] if columns.date_index is not None and len(row) > columns.date_index else ""
            time_text = row[columns.time_index] if columns.time_index is not None and len(row) > columns.time_index else ""
            try:
                timestamp, provenance = combine_timestamp(date_text, time_text)
            except AmbiguousDateTime:
                row_ambiguous = True
                timestamp, provenance = None, "ambiguous"
            provenances.add(provenance)
        if timestamp is None and result.has_nmea:
            recovered, consistent = nmea_timestamp(",".join(row))
            if recovered is not None and consistent:
                timestamp = recovered
                row_ambiguous = False
                result.nmea_recovered_rows += 1
                provenances.add("embedded-nmea-rmc")
            elif recovered is not None:
                result.nmea_inconsistent_rows += 1
        if row_ambiguous:
            result.ambiguous_time_rows += 1
        point = TrackPoint(lon=lon, lat=lat, timestamp=timestamp, row_index=row_index)
        if columns.absolute_alt_index is not None and len(row) > columns.absolute_alt_index:
            point.absolute_alt = _float(row[columns.absolute_alt_index])
        if columns.relative_alt_index is not None and len(row) > columns.relative_alt_index:
            point.relative_alt = _float(row[columns.relative_alt_index])
        result.points.append(point)
    usable = {value for value in provenances if value not in {"none", "ambiguous"}}
    if usable:
        result.timestamp_provenance = sorted(usable)[0]
    elif "ambiguous" in provenances:
        result.timestamp_provenance = "ambiguous"
    return result


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

METRES_PER_DEGREE = 111_320.0


def metre_distance(first: TrackPoint, second: TrackPoint) -> float:
    mean_lat = math.radians((first.lat + second.lat) / 2.0)
    dx = (second.lon - first.lon) * METRES_PER_DEGREE * math.cos(mean_lat)
    dy = (second.lat - first.lat) * METRES_PER_DEGREE
    return math.hypot(dx, dy)


def median_interval_seconds(points: list[TrackPoint]) -> float | None:
    deltas = [
        (second.timestamp - first.timestamp).total_seconds()
        for first, second in zip(points, points[1:])
        if first.timestamp is not None and second.timestamp is not None
    ]
    positive = sorted(delta for delta in deltas if delta > 0)
    if not positive:
        return None
    middle = len(positive) // 2
    if len(positive) % 2:
        return positive[middle]
    return (positive[middle - 1] + positive[middle]) / 2.0


def split_segments(
    points: list[TrackPoint],
    time_gap_seconds: float,
    distance_gap_m: float,
    min_points: int,
) -> list[list[TrackPoint]]:
    """Break a point sequence wherever the recording is discontinuous.

    A gap in time or an implausible jump in space marks the end of one
    acquisition. The two sides are never joined by a straight line.
    """
    segments: list[list[TrackPoint]] = []
    current: list[TrackPoint] = []
    for point in points:
        if current:
            previous = current[-1]
            gap = False
            if previous.timestamp is not None and point.timestamp is not None:
                delta = (point.timestamp - previous.timestamp).total_seconds()
                if delta < 0 or delta > time_gap_seconds:
                    gap = True
            if metre_distance(previous, point) > distance_gap_m:
                gap = True
            if gap:
                segments.append(current)
                current = []
        current.append(point)
    if current:
        segments.append(current)
    return [segment for segment in segments if len(segment) >= min_points]


def deduplicate_consecutive(points: list[TrackPoint]) -> list[TrackPoint]:
    result: list[TrackPoint] = []
    for point in points:
        if result and abs(point.lon - result[-1].lon) < 1e-9 and abs(point.lat - result[-1].lat) < 1e-9:
            continue
        result.append(point)
    return result


def simplify(points: list[TrackPoint], tolerance_m: float) -> list[TrackPoint]:
    """Ramer-Douglas-Peucker in a local metric frame, iterative to stay safe on
    long 10 Hz acquisitions."""
    if len(points) <= 2 or tolerance_m <= 0:
        return points
    lat0 = sum(point.lat for point in points) / len(points)
    lon0 = sum(point.lon for point in points) / len(points)
    cos_lat0 = math.cos(math.radians(lat0))
    projected = [
        ((point.lon - lon0) * METRES_PER_DEGREE * cos_lat0, (point.lat - lat0) * METRES_PER_DEGREE)
        for point in points
    ]

    def distance(index, start, end):
        px, py = projected[index]
        ax, ay = projected[start]
        bx, by = projected[end]
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        furthest, maximum = -1, -1.0
        for index in range(start + 1, end):
            candidate = distance(index, start, end)
            if candidate > maximum:
                furthest, maximum = index, candidate
        if maximum > tolerance_m and furthest > start:
            keep[furthest] = True
            stack.append((start, furthest))
            stack.append((furthest, end))
    return [point for point, flag in zip(points, keep) if flag]
