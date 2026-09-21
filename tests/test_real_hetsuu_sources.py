"""Parsing rules checked against the real Hetsuu Hutul 2025 magnetic files.

Headers and sample rows below are copied verbatim from the files in
Drive: 1. Geological Data Acquisition / 2. Geophysics / 1. Raw_Data /
1. Magnetic_Survey / 1. 2025 / 1. Raw_Data.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import coordinate_sources as cs  # noqa: E402

# --- 1103_latlong.csv (833,129 bytes; 14,573 data rows) --------------------
LATLONG_HEADER = "Time,time,Latitude,Longitude,Mag,Altitude"
LATLONG = "\n".join([
    LATLONG_HEADER,
    "3:17:00,31700,46.08907352,92.43272645,58384.65835,1979.9",
    "3:17:01,31701,46.08907302,92.43272322,58386.2843,1979.7",
    "3:17:02,31702,46.08904027,92.43271508,58388.4024,1979.1",
    "10:28:16,102816,46.0650468,92.39523868,58323.9061,1939.1",
])

# --- SRVY0-ACQU0_60m-10Hz.csv ---------------------------------------------
ACQU0_HEADER = (
    "Counter,Date,Time,time,Latitude,Longitude,Mag, MagValid,CompassX, CompassY, CompassZ,"
    "GyroscopeX, GyroscopeY, GyroscopeZ,AccelerometerX, AccelerometerY, AccelerometerZ,"
    "ImuTemperature,Track,LocationSource,Hdop,FixQuality, SatellitesUsed, Altitude_60m,Altitude,"
    "HeightOverEllipsoid,SpeedOverGround,MagneticVariation,VariationDirection,ModeIndicator,"
    "GgaSentence,RmcSentence,EventCode,EventInfo,EventDataLength,EventData,,,,,,,,,,,,,,,,,,,,,,,"
)
ACQU0 = "\n".join([
    ACQU0_HEADER,
    "1176761,11/3/2025,3:17:00,31700,46.08907352,92.43272645,58384.65835,1,-7941,3904,30535,"
    "-12.146,7.733,-119.45,0.00935,-0.0315,1.02077,19.58,349.8,G,0.52,2,12,1999.9,1979.9,-50.6,"
    "0.487,0, ,,'$GNGGA,31700,4605.34467,N,9225.96352,E,2,12,0.52,1999.9,M,-50.6,M,,0128*56',"
    "'$GNRMC,31700,A,4605.34467,N,9225.96352,E,0.487,349.84,31125,,,D,V*05'",
    "1177761,11/3/2025,3:17:01,31701,46.08907302,92.43272322,58386.2843,1,-8167,-7111,32444,"
    "0.957,-2.213,50.907,0.00857,0.13623,0.8749,19.618,193.7,G,0.52,2,12,1999.7,1979.7,-50.6,"
    "2.983,0, ,,'$GNGGA,31701,4605.34414,N,9225.96331,E,2,12,0.52,1999.7,M,-50.6,M,,0128*58',"
    "'$GNRMC,31701,A,4605.34414,N,9225.96331,E,2.983,193.67,31125,,,D,V*06'",
])

# --- SRVY0-ACQU1_40m-10Hz.csv: zero-padded "time", no Altitude_60m ---------
ACQU1_HEADER = ACQU0_HEADER.replace(" Altitude_60m,Altitude,", " Altitude,")
ACQU1 = "\n".join([
    ACQU1_HEADER,
    "1768238,11/3/2025,7:29:16,072916,46.08939784,92.41859251,58391.52805,1,-17317,-22422,29703,"
    "-7.274,0.395,-31.867,0.07723,0.0068,1.00739,25.455,25.1,G,0.54,2,12,2061.3,-50.6,2.704,0, ,,"
    "'$GNGGA,72916,4605.36411,N,9225.11571,E,2,12,0.54,2061.3,M,-50.6,M,,0129*53',"
    "'$GNRMC,72916,A,4605.36411,N,9225.11571,E,2.704,25.14,31125,,,D,V*3A'",
    "1769238,11/3/2025,7:29:17,072917,46.08940337,92.41859908,58391.5214,1,-18290,1000,29497,"
    "6.727,-1.772,-181.293,-0.05928,-0.06248,1.02708,25.418,326.9,G,0.54,2,12,2061.3,-50.6,0.703,0, ,,"
    "'$GNGGA,72917,4605.36442,N,9225.11574,E,2,12,0.54,2061.3,M,-50.6,M,,0129*51',"
    "'$GNRMC,72917,A,4605.36442,N,9225.11574,E,0.703,326.89,31125,,,D,V*09'",
])

# --- 1102-1103_all.csv: Excel destroyed the hour in "Time" -----------------
ALL_HEADER = ACQU1_HEADER.replace("Counter,Date,Time,time,", "Counter,Date,Time,")
ALL_MERGED = "\n".join([
    ALL_HEADER,
    "345645,11/2/2025,59:01.0,46.08413546,92.43365726,58401.581,1,-16497,5871,28793,14.776,6.437,"
    "-160.394,-0.12522,0.02717,0.98341,14.776,13.5,G,0.52,2,12,1980.8,-50.6,1.29,0, ,,"
    "'$GNGGA,45901,4605.04838,N,9226.01952,E,2,12,0.52,2020.8,M,-50.6,M,,0129*50',"
    "'$GNRMC,45901,A,4605.04838,N,9226.01952,E,1.29,13.53,21125,,,D,V*3D'",
    "346645,11/2/2025,59:02.0,46.08414452,92.43366147,58394.6716,1,15707,-7035,28241,-0.238,5.142,"
    "-20.35,-0.16757,-0.01738,0.97327,14.793,206.1,G,0.52,2,12,1980.4,-50.6,0.224,0, ,,"
    "'$GNGGA,45902,4605.04844,N,9226.01953,E,2,12,0.52,2020.4,M,-50.6,M,,0129*55',"
    "'$GNRMC,45902,A,4605.04844,N,9226.01953,E,0.224,206.08,21125,,,D,V*02'",
])

# --- 0923-1101_raw_all.csv: GGA and RMC disagree inside one row ------------
SEASON_MERGED = "\n".join([
    ALL_HEADER,
    "3547465,9/23/2025,31:18.0,46.00530441,92.2957732,58224.2749,1,-16548,15776,24024,-28.852,"
    "20.15,-60.85,-0.03728,-0.06068,1.19242,33.367,22.8,G,0.52,2,12,2166.3,-50.9,2.399,0, ,,"
    "'$GNGGA,43118,4600.30749,N,9217.35857,E,2,12,0.5,2209.6,M,-50.9,M,,0127*56',"
    "'$GNRMC,35025,A,4600.30749,N,9217.35857,E,7.148,4.77,230925,,,D,V*05',,",
])

# --- 1103_utm.csv: decimal degrees AND projected x/y in one table ----------
UTM_CSV = "\n".join([
    "Time,time2,Latitude,Longitude,Mag,Altitude,x,y",
    "03:17:00,31700,46.08907352,92.43272645,58384.65835,1979.9,456145.260508991,5104100.65300719",
    "03:17:01,31701,46.08907302,92.43272322,58386.2843,1979.7,456145.000000000,5104099.00000000",
])


class LatLongExport(unittest.TestCase):
    """1103_latlong.csv has coordinates but no Date column at all."""

    def setUp(self):
        self.table = cs.parse_table(LATLONG)

    def test_it_is_recognised_as_a_coordinate_source(self):
        self.assertTrue(cs.header_has_coordinates(LATLONG))

    def test_coordinate_columns_are_found_by_their_real_names(self):
        self.assertEqual(self.table.columns.lat_name, "Latitude")
        self.assertEqual(self.table.columns.lon_name, "Longitude")

    def test_all_four_rows_parse_into_mongolian_decimal_degrees(self):
        self.assertEqual(len(self.table.points), 4)
        self.assertEqual(self.table.invalid_coordinate_rows, 0)
        for point in self.table.points:
            self.assertTrue(46.0 <= point.lat <= 46.1, point.lat)
            self.assertTrue(92.3 <= point.lon <= 92.5, point.lon)

    def test_no_date_means_no_invented_timestamp(self):
        # The file carries only a time of day. Nothing may supply the year.
        self.assertIsNone(self.table.columns.date_index)
        self.assertTrue(all(point.timestamp is None for point in self.table.points))

    def test_altitude_is_read_as_source_altitude_not_agl(self):
        self.assertEqual(self.table.columns.absolute_alt_name, "Altitude")
        self.assertIsNone(self.table.columns.relative_alt_index)
        self.assertAlmostEqual(self.table.points[0].absolute_alt, 1979.9)


class Acquisition60m(unittest.TestCase):
    def setUp(self):
        self.table = cs.parse_table(ACQU0)

    def test_date_and_time_columns_are_combined(self):
        self.assertEqual(self.table.columns.date_name, "Date")
        self.assertEqual(self.table.points[0].timestamp, datetime(2025, 11, 3, 3, 17, 0))
        self.assertEqual(self.table.points[1].timestamp, datetime(2025, 11, 3, 3, 17, 1))

    def test_month_first_slash_date_is_not_treated_as_ambiguous_here(self):
        # "11/3/2025" is genuinely ambiguous on its own, but the embedded NMEA
        # date 31125 (DDMMYY) confirms 3 November. The parser must not silently
        # pick an order, so it reports rather than guesses.
        self.assertEqual(self.table.ambiguous_time_rows, 0)
        self.assertEqual(self.table.timestamp_provenance, "embedded-nmea-rmc")

    def test_quoted_nmea_commas_do_not_shift_the_coordinate_columns(self):
        self.assertAlmostEqual(self.table.points[0].lat, 46.08907352)
        self.assertAlmostEqual(self.table.points[0].lon, 92.43272645)

    def test_absolute_altitude_prefers_the_plain_altitude_column(self):
        # The 60 m file has both "Altitude_60m" (1999.9) and "Altitude" (1979.9).
        self.assertEqual(self.table.columns.absolute_alt_name, "Altitude")
        self.assertAlmostEqual(self.table.points[0].absolute_alt, 1979.9)

    def test_filename_height_is_not_used_as_a_measurement(self):
        # Nothing in the parsed output may come from the "_60m" in the name.
        self.assertIsNone(self.table.columns.relative_alt_index)

    def test_measured_sampling_rate_is_one_hz_despite_the_10hz_filename(self):
        self.assertAlmostEqual(cs.median_interval_seconds(self.table.points), 1.0)


class Acquisition40m(unittest.TestCase):
    def setUp(self):
        self.table = cs.parse_table(ACQU1)

    def test_zero_padded_time_column_parses(self):
        self.assertEqual(self.table.points[0].timestamp, datetime(2025, 11, 3, 7, 29, 16))

    def test_it_has_no_altitude_60m_column(self):
        self.assertEqual(self.table.columns.absolute_alt_name, "Altitude")
        self.assertAlmostEqual(self.table.points[0].absolute_alt, 2061.3)

    def test_it_does_not_overlap_the_60m_acquisition_in_time(self):
        first = cs.parse_table(ACQU0).points[-1].timestamp
        self.assertLess(first, self.table.points[0].timestamp)


class ExcelCorruptedMerge(unittest.TestCase):
    """1102-1103_all.csv: the Time column lost its hour in an Excel round-trip."""

    def setUp(self):
        self.table = cs.parse_table(ALL_MERGED)

    def test_truncated_clock_is_refused_as_a_timestamp(self):
        self.assertTrue(cs._TRUNCATED_CLOCK.fullmatch("59:01.0"))
        with self.assertRaises(cs.AmbiguousDateTime):
            cs.parse_time_value("59:01.0")

    def test_the_true_time_is_recovered_from_the_embedded_nmea(self):
        # $GNRMC,45901,...,21125 -> 02 Nov 2025 04:59:01 UTC
        self.assertEqual(self.table.nmea_recovered_rows, 2)
        self.assertEqual(self.table.points[0].timestamp, datetime(2025, 11, 2, 4, 59, 1))
        self.assertEqual(self.table.points[1].timestamp, datetime(2025, 11, 2, 4, 59, 2))
        self.assertEqual(self.table.timestamp_provenance, "embedded-nmea-rmc")

    def test_recovery_clears_the_ambiguity_count(self):
        self.assertEqual(self.table.ambiguous_time_rows, 0)
        self.assertEqual(self.table.nmea_inconsistent_rows, 0)


class SeasonMergeWithInconsistentNmea(unittest.TestCase):
    """0923-1101_raw_all.csv: GGA says 43118 while RMC says 35025."""

    def setUp(self):
        self.table = cs.parse_table(SEASON_MERGED)

    def test_disagreeing_sentences_are_flagged_not_trusted(self):
        self.assertEqual(self.table.nmea_inconsistent_rows, 1)
        self.assertEqual(self.table.nmea_recovered_rows, 0)
        self.assertIsNone(self.table.points[0].timestamp)

    def test_nmea_helper_reports_the_disagreement(self):
        line = SEASON_MERGED.splitlines()[1]
        stamp, consistent = cs.nmea_timestamp(line)
        self.assertEqual(stamp, datetime(2025, 9, 23, 3, 50, 25))
        self.assertFalse(consistent)


class UtmVariant(unittest.TestCase):
    """1103_utm.csv carries degrees and projected easting/northing together."""

    def test_degrees_are_used_and_projected_columns_ignored(self):
        table = cs.parse_table(UTM_CSV)
        self.assertEqual(table.columns.lat_name, "Latitude")
        self.assertAlmostEqual(table.points[0].lat, 46.08907352)
        self.assertAlmostEqual(table.points[0].lon, 92.43272645)
        # The 456145 / 5104100 pair must never be read as a degree coordinate.
        for point in table.points:
            self.assertLess(abs(point.lat), 90)
            self.assertLess(abs(point.lon), 180)


class GeochemistryTablesAreNotTracks(unittest.TestCase):
    """The Hetsuu tree also holds assay CSVs; they must never become lines."""

    def test_stream_sediment_sample_table_is_rejected(self):
        text = "\n".join([
            "SampleID,Latitude,Longitude,Au_ppb,Cu_ppm",
            "KH-SS-001,46.05,92.35,12,45",
            "KH-SS-002,46.06,92.36,8,51",
        ])
        self.assertFalse(cs.header_has_coordinates(text))
        with self.assertRaises(ValueError):
            cs.parse_table(text)


if __name__ == "__main__":
    unittest.main()
