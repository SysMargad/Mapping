"""Detection and parsing rules for coordinate-bearing survey sources."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import coordinate_sources as cs  # noqa: E402


LATLONG_CSV = "\n".join([
    "Latitude,Longitude,Date,Time,Altitude",
    "49.1501,113.5002,2025-11-03,09:00:00.0,1180.4",
    "49.1502,113.5003,2025-11-03,09:00:00.1,1180.6",
    "49.1503,113.5004,2025-11-03,09:00:00.2,1180.9",
])

GCP_CSV = "\n".join([
    "PointName,Latitude,Longitude,Elevation",
    "GCP-01,49.1500,113.5000,1180.0",
    "GCP-02,49.1600,113.5100,1182.0",
    "GCP-03,49.1700,113.5200,1184.0",
])

SAMPLE_CSV = "\n".join([
    "SampleID,lat,lon,Au_ppm",
    "S-001,49.1500,113.5000,0.12",
    "S-002,49.1600,113.5100,0.08",
])


class DetectColumns(unittest.TestCase):
    def test_latitude_longitude_header_is_recognised(self):
        # Requirement 8.1: a file named 1103_latlong.csv must be recognised by
        # its header, not skipped because the name has no DJI/SRVY token.
        self.assertTrue(cs.header_has_coordinates(LATLONG_CSV))
        columns = cs.detect_columns(cs.read_header(LATLONG_CSV))
        self.assertEqual(columns.lat_name, "Latitude")
        self.assertEqual(columns.lon_name, "Longitude")
        self.assertEqual(columns.date_name, "Date")
        self.assertEqual(columns.time_name, "Time")

    def test_short_lat_lon_aliases(self):
        columns = cs.detect_columns(["lat", "lon", "height"])
        self.assertEqual((columns.lat_index, columns.lon_index), (0, 1))

    def test_gps_prefixed_aliases(self):
        columns = cs.detect_columns(["GPSLatitude", "GPSLongitude"])
        self.assertEqual((columns.lat_index, columns.lon_index), (0, 1))

    def test_header_without_coordinates_is_rejected(self):
        self.assertIsNone(cs.detect_columns(["time", "field_nT", "quality"]))

    def test_latitude_is_never_reused_as_longitude(self):
        columns = cs.detect_columns(["Latitude", "Longitude"])
        self.assertNotEqual(columns.lat_index, columns.lon_index)

    def test_tab_separated_header(self):
        self.assertTrue(cs.header_has_coordinates("Latitude\tLongitude\tTime\n49.1\t113.5\t09:00:00\n"))


class PointTableRejection(unittest.TestCase):
    def test_gcp_table_is_not_a_trajectory(self):
        # Requirement 8.2: a GCP/sample coordinate table must never become a
        # flight track, even though it does carry latitude/longitude.
        self.assertTrue(cs.looks_like_point_table(cs.read_header(GCP_CSV)))
        self.assertFalse(cs.header_has_coordinates(GCP_CSV))
        with self.assertRaises(ValueError):
            cs.parse_table(GCP_CSV)

    def test_sample_assay_table_is_not_a_trajectory(self):
        self.assertFalse(cs.header_has_coordinates(SAMPLE_CSV))
        with self.assertRaises(ValueError):
            cs.parse_table(SAMPLE_CSV)


class Timestamps(unittest.TestCase):
    def test_date_and_time_columns_are_combined(self):
        table = cs.parse_table(LATLONG_CSV)
        self.assertEqual(table.points[0].timestamp, datetime(2025, 11, 3, 9, 0, 0))
        self.assertEqual(table.timestamp_provenance, "source-date+time-columns")

    def test_truncated_clock_is_not_a_timestamp(self):
        # "59:01.0" has no hour and no date; it must not become a timestamp.
        with self.assertRaises(cs.AmbiguousDateTime):
            cs.parse_time_value("59:01.0")

    def test_ambiguous_day_month_order_is_reported(self):
        with self.assertRaises(cs.AmbiguousDateTime):
            cs.parse_date_value("03/11/2025")

    def test_ambiguous_rows_are_counted_not_guessed(self):
        text = "\n".join([
            "Latitude,Longitude,Date,Time",
            "49.1501,113.5002,,59:01.0",
            "49.1502,113.5003,,59:02.0",
        ])
        table = cs.parse_table(text)
        self.assertEqual(table.ambiguous_time_rows, 2)
        self.assertEqual(table.timestamp_provenance, "ambiguous")
        self.assertTrue(all(point.timestamp is None for point in table.points))

    def test_iso_datetime_column(self):
        text = "Latitude,Longitude,Timestamp\n49.15,113.50,2025-11-03T09:00:00\n49.16,113.51,2025-11-03T09:00:01\n"
        table = cs.parse_table(text)
        self.assertEqual(table.points[0].timestamp, datetime(2025, 11, 3, 9, 0, 0))


class CoordinateValidation(unittest.TestCase):
    def test_utm_coordinates_are_rejected_not_reinterpreted(self):
        text = "Latitude,Longitude\n5445000.0,608000.0\n5445010.0,608010.0\n"
        table = cs.parse_table(text)
        self.assertEqual(table.points, [])
        self.assertEqual(table.invalid_coordinate_rows, 2)

    def test_zero_zero_and_blank_rows_are_dropped(self):
        text = "Latitude,Longitude\n0,0\n,\nNaN,113.5\n49.15,113.50\n49.16,113.51\n"
        table = cs.parse_table(text)
        self.assertEqual(len(table.points), 2)
        # A wholly blank line is skipped without being counted; 0/0 and NaN are
        # real rows carrying unusable coordinates.
        self.assertEqual(table.invalid_coordinate_rows, 2)
        self.assertEqual(table.row_count, 4)


class Segmentation(unittest.TestCase):
    @staticmethod
    def _point(lon, lat, second):
        return cs.TrackPoint(lon=lon, lat=lat, timestamp=datetime(2025, 11, 3, 9, 0, 0) + __import__("datetime").timedelta(seconds=second))

    def test_time_gap_splits_acquisitions(self):
        # Requirement 8.5: no straight line may be drawn across a recording gap.
        points = [self._point(113.50 + index * 0.0001, 49.15, index) for index in range(5)]
        points += [self._point(113.60 + index * 0.0001, 49.20, 3600 + index) for index in range(5)]
        segments = cs.split_segments(points, time_gap_seconds=60, distance_gap_m=500, min_points=2)
        self.assertEqual(len(segments), 2)
        self.assertEqual(len(segments[0]), 5)
        self.assertEqual(len(segments[1]), 5)

    def test_distance_jump_splits_even_without_time(self):
        points = [cs.TrackPoint(lon=113.50 + index * 0.0001, lat=49.15) for index in range(4)]
        points += [cs.TrackPoint(lon=114.50 + index * 0.0001, lat=49.15) for index in range(4)]
        segments = cs.split_segments(points, time_gap_seconds=60, distance_gap_m=500, min_points=2)
        self.assertEqual(len(segments), 2)

    def test_short_segments_are_discarded(self):
        points = [self._point(113.50, 49.15, 0), self._point(113.60, 49.20, 3600)]
        self.assertEqual(cs.split_segments(points, 60, 500, min_points=2), [])

    def test_median_interval_is_measured_from_data(self):
        points = [self._point(113.50 + index * 0.0001, 49.15, index * 0.1) for index in range(11)]
        interval = cs.median_interval_seconds(points)
        self.assertAlmostEqual(interval, 0.1, places=6)

    def test_simplify_keeps_endpoints_and_reduces_points(self):
        points = [cs.TrackPoint(lon=113.50 + index * 0.00001, lat=49.15) for index in range(200)]
        reduced = cs.simplify(points, tolerance_m=2.5)
        self.assertLess(len(reduced), len(points))
        self.assertEqual(reduced[0].lon, points[0].lon)
        self.assertEqual(reduced[-1].lon, points[-1].lon)


class ReasonCodes(unittest.TestCase):
    def test_required_audit_codes_exist(self):
        required = {
            "IMPORTED", "MISSING_SOURCE", "ACCESS_DENIED", "UNSUPPORTED_FLIGHT_LOG",
            "INVALID_COORDINATES", "AMBIGUOUS_DATE_TIME", "DATE_MISMATCH",
            "MISSION_UNMATCHED", "AMBIGUOUS_MATCH", "DUPLICATE_SOURCE",
            "WRONG_PROJECT_LOCATION",
        }
        self.assertTrue(required.issubset(cs.REASON_CODES))


if __name__ == "__main__":
    unittest.main()
