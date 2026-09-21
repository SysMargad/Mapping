"""Campaign import keeps 2025 survey data separate from 2026 tracker missions."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import coordinate_sources as cs  # noqa: E402
import import_campaign_tracks as ict  # noqa: E402

# Real Hetsuu Hutul licence extent: lon 92.289..92.510, lat 46.000..46.132
LON0, LAT0 = 92.35, 46.05
CAMPAIGN_ID = "hetsuu-hutul-2025-magnetic"

CAMPAIGNS = {
    "campaigns": [{
        "id": CAMPAIGN_ID,
        "projectKey": "hetsuu-hutul",
        "label": "2025 Соронзон хэмжилтийн GPS зам",
        "campaignYear": 2025,
        "sensor": "Magnetic survey",
        "sensorVerified": False,
        "gnssSource": "unverified",
        "timeGapSeconds": 30,
        "distanceGapM": 250,
        "minSegmentPoints": 5,
        "simplifyToleranceM": 0.5,
        "acquisitionFilePatterns": [r"SRVY\d+-ACQU\d+"],
        "match": {"pathContains": ["magnetic"], "fileNamePatterns": [r"\.csv$"]},
    }],
}

LICENCES = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"LICENSE": "XV-022905"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [92.28878, 46.00038], [92.51043, 46.00038],
            [92.51043, 46.13214], [92.28878, 46.13214], [92.28878, 46.00038],
        ]]},
    }],
}


def line_csv(count=40, start_second=0.0, lon0=LON0, lat0=LAT0, date="2025-11-03", step=0.00005):
    rows = ["Latitude,Longitude,Date,Time,Altitude"]
    for index in range(count):
        second = start_second + index * 0.1
        clock = f"09:{int(second // 60) % 60:02d}:{second % 60:04.1f}"
        rows.append(f"{lat0 + index * step:.6f},{lon0 + index * step:.6f},{date},{clock},1180.{index % 10}")
    return "\n".join(rows) + "\n"


class CampaignImportCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.campaigns_path = self.root / "campaigns.json"
        self.campaigns_path.write_text(json.dumps(CAMPAIGNS), encoding="utf-8")
        self.licences_path = self.root / "licences.geojson"
        self.licences_path.write_text(json.dumps(LICENCES), encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, text):
        (self.sources / name).write_text(text, encoding="utf-8")

    def run_import(self, entries, output_name="campaign-tracks.geojson", existing=None):
        mapping = self.root / "mapping.json"
        mapping.write_text(json.dumps({"sources": entries}, ensure_ascii=False), encoding="utf-8")
        output = self.root / output_name
        audit = self.root / "audit.json"
        args = argparse.Namespace(
            source_dir=self.sources, project="hetsuu-hutul",
            campaigns=self.campaigns_path, licences=self.licences_path,
            mapping=mapping, existing=existing, output=output, audit=audit,
        )
        report = ict.build(args)
        return report, json.loads(output.read_text(encoding="utf-8"))

    @staticmethod
    def entry(name, campaign_id=CAMPAIGN_ID, **extra):
        return {"file": name, "campaignId": campaign_id, **extra}


class ImportsCampaignGeometry(CampaignImportCase):
    def test_magnetic_csv_becomes_a_campaign_track(self):
        self.write("1103_latlong.csv", line_csv())
        report, data = self.run_import([self.entry("1103_latlong.csv")])
        self.assertEqual(report["importedSources"], 1)
        self.assertEqual(len(data["features"]), 1)
        props = data["features"][0]["properties"]
        self.assertEqual(props["campaignId"], CAMPAIGN_ID)
        self.assertEqual(props["campaignYear"], 2025)
        self.assertEqual(props["sensor"], "Magnetic survey")
        self.assertFalse(props["sensorVerified"])
        self.assertEqual(props["dataType"], "survey_campaign_track")

    def test_geojson_coordinates_are_longitude_first(self):
        self.write("1103_latlong.csv", line_csv())
        _, data = self.run_import([self.entry("1103_latlong.csv")])
        lon, lat = data["features"][0]["geometry"]["coordinates"][0]
        self.assertAlmostEqual(lon, LON0, places=4)
        self.assertAlmostEqual(lat, LAT0, places=4)

    def test_no_tracker_mission_is_attached(self):
        # Requirement 8.3: 2025 campaign data must never borrow a 2026 L2
        # tracker mission, tracker id, or tracker date.
        self.write("1103_latlong.csv", line_csv())
        _, data = self.run_import([self.entry("1103_latlong.csv")])
        props = data["features"][0]["properties"]
        for forbidden in ("trackerId", "mission", "flightRecordVerified"):
            self.assertNotIn(forbidden, props)
        self.assertNotIn("2026", json.dumps(props, ensure_ascii=False))

    def test_sampling_interval_is_measured_from_the_data(self):
        self.write("1103_latlong.csv", line_csv())
        _, data = self.run_import([self.entry("1103_latlong.csv")])
        self.assertAlmostEqual(data["features"][0]["properties"]["samplingIntervalSeconds"], 0.1, places=3)

    def test_altitude_column_is_not_labelled_agl(self):
        self.write("1103_latlong.csv", line_csv())
        _, data = self.run_import([self.entry("1103_latlong.csv")])
        props = data["features"][0]["properties"]
        self.assertEqual(props["sourceAltitudeReference"], "source-provided GNSS/absolute altitude")
        self.assertNotIn("flightHeightMeanM", props)

    def test_plain_date_time_columns_keep_the_declared_timezone(self):
        self.write("1103_latlong.csv", line_csv())
        _, data = self.run_import([self.entry("1103_latlong.csv")])
        self.assertEqual(data["features"][0]["properties"]["timeZone"], "unverified")

    def test_nmea_derived_timestamps_are_marked_utc(self):
        rows = [
            "Counter,Date,Time,Latitude,Longitude,GgaSentence,RmcSentence",
        ]
        for index in range(30):
            second = index + 1
            rows.append(
                f"{index},11/3/2025,59:{second:02d}.0,{LAT0 + index * 0.00005:.6f},"
                f"{LON0 + index * 0.00005:.6f},"
                f"'$GNGGA,4590{second % 10},x','$GNRMC,4590{second % 10},A,x,N,y,E,1,2,31125,,,D,V*05'"
            )
        self.write("merged.csv", "\n".join(rows) + "\n")
        _, data = self.run_import([self.entry("merged.csv")])
        props = data["features"][0]["properties"]
        self.assertEqual(props["timestampProvenance"], "embedded-nmea-rmc")
        self.assertEqual(props["timeZone"], "UTC (NMEA RMC)")

    def test_coverage_is_not_claimed(self):
        # Requirement 6: a 50 m buffer must not be reported as surveyed area.
        self.write("1103_latlong.csv", line_csv())
        _, data = self.run_import([self.entry("1103_latlong.csv")])
        self.assertEqual(data["features"][0]["properties"]["coverageStatus"], "not_calculated")


class Segmentation(CampaignImportCase):
    def test_time_gap_creates_separate_segments(self):
        # Requirement 8.5: two acquisitions must not be joined by a fake line.
        first = line_csv(count=20, start_second=0)
        second = line_csv(count=20, start_second=3600, lon0=LON0 + 0.02, lat0=LAT0 + 0.02)
        self.write("both.csv", first + "\n".join(second.splitlines()[1:]) + "\n")
        _, data = self.run_import([self.entry("both.csv")])
        self.assertEqual(len(data["features"]), 2)
        self.assertEqual(data["features"][0]["properties"]["segmentCount"], 2)
        ends = data["features"][0]["geometry"]["coordinates"][-1]
        starts = data["features"][1]["geometry"]["coordinates"][0]
        self.assertNotEqual(ends, starts)

    def test_missing_timestamps_mark_the_source_for_review(self):
        rows = ["Latitude,Longitude"] + [
            f"{LAT0 + index * 0.00005:.6f},{LON0 + index * 0.00005:.6f}" for index in range(30)
        ]
        self.write("notime.csv", "\n".join(rows) + "\n")
        _, data = self.run_import([self.entry("notime.csv")])
        props = data["features"][0]["properties"]
        self.assertEqual(props["status"], "review_required")
        self.assertIn(cs.AMBIGUOUS_DATE_TIME, props["qaFlags"])


class Deduplication(CampaignImportCase):
    def test_merged_export_is_dropped_in_favour_of_per_acquisition_files(self):
        # Requirement 8.6: raw per-acquisition files and the merged "all" export
        # hold the same measurements, so only one of them may be imported.
        acqu0 = line_csv(count=30, start_second=0)
        acqu1 = line_csv(count=30, start_second=0, lon0=LON0 + 0.03, lat0=LAT0 + 0.03)
        merged = acqu0 + "\n".join(acqu1.splitlines()[1:]) + "\n"
        self.write("SRVY0-ACQU0_60m-10Hz.csv", acqu0)
        self.write("SRVY0-ACQU1_40m-10Hz.csv", acqu1)
        self.write("1102-1103_all.csv", merged)
        report, data = self.run_import([
            self.entry("1102-1103_all.csv"),
            self.entry("SRVY0-ACQU0_60m-10Hz.csv"),
            self.entry("SRVY0-ACQU1_40m-10Hz.csv"),
        ])
        imported = {item["file"] for item in report["audit"] if item["reason"] == cs.IMPORTED}
        duplicates = [item for item in report["audit"] if item["reason"] == cs.DUPLICATE_SOURCE]
        self.assertEqual(imported, {"SRVY0-ACQU0_60m-10Hz.csv", "SRVY0-ACQU1_40m-10Hz.csv"})
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["file"], "1102-1103_all.csv")
        self.assertEqual(duplicates[0]["basis"], "coordinate-overlap")
        self.assertEqual(duplicates[0]["canonicalFiles"],
                         ["SRVY0-ACQU0_60m-10Hz.csv", "SRVY0-ACQU1_40m-10Hz.csv"])
        self.assertTrue(all("all" not in feature["properties"]["sourceFile"] for feature in data["features"]))

    def test_identical_checksum_is_reported_as_duplicate(self):
        text = line_csv()
        self.write("a.csv", text)
        self.write("b.csv", text)
        report, _ = self.run_import([self.entry("a.csv"), self.entry("b.csv")])
        duplicates = [item for item in report["audit"] if item["reason"] == cs.DUPLICATE_SOURCE]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["basis"], "checksum")


class Rejections(CampaignImportCase):
    def test_gcp_table_is_not_imported(self):
        # Requirement 8.2
        self.write("gcp.csv", "PointName,Latitude,Longitude\nGCP-1,46.05,92.35\nGCP-2,46.06,92.36\n")
        report, data = self.run_import([self.entry("gcp.csv")])
        self.assertEqual(data["features"], [])
        self.assertEqual(report["audit"][0]["reason"], cs.NOT_A_TRAJECTORY)

    def test_other_project_coordinates_are_rejected(self):
        # Nergui Undur coordinates must never land in the Hetsuu dataset.
        self.write("wrong.csv", line_csv(lon0=113.50, lat0=49.15))
        report, data = self.run_import([self.entry("wrong.csv")])
        self.assertEqual(data["features"], [])
        self.assertEqual(report["audit"][0]["reason"], cs.WRONG_PROJECT_LOCATION)

    def test_undeclared_source_is_not_imported(self):
        self.write("mystery.csv", line_csv())
        report, data = self.run_import([{"file": "mystery.csv", "campaignId": "unknown-campaign"}])
        self.assertEqual(data["features"], [])
        self.assertEqual(report["audit"][0]["reason"], cs.MISSION_UNMATCHED)

    def test_missing_local_file_is_reported(self):
        report, _ = self.run_import([self.entry("absent.csv")])
        self.assertEqual(report["audit"][0]["reason"], cs.MISSING_SOURCE)

    def test_every_audit_row_uses_a_known_reason_code(self):
        self.write("ok.csv", line_csv())
        self.write("gcp.csv", "PointName,Latitude,Longitude\nGCP-1,46.05,92.35\n")
        report, _ = self.run_import([self.entry("ok.csv"), self.entry("gcp.csv")])
        for item in report["audit"]:
            self.assertIn(item["reason"], cs.REASON_CODES)


class Idempotency(CampaignImportCase):
    def test_two_runs_produce_identical_output(self):
        # Requirement 8.10
        self.write("SRVY0-ACQU0_60m-10Hz.csv", line_csv(count=30))
        entries = [self.entry("SRVY0-ACQU0_60m-10Hz.csv")]
        _, first = self.run_import(entries, "one.geojson")
        _, second = self.run_import(entries, "two.geojson", existing=self.root / "one.geojson")
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )
        self.assertEqual(first["featureCount"], 1)

    def test_another_campaign_is_not_wiped_by_a_run_with_no_sources(self):
        # Requirement 7: a missing source must never blank existing tracks.
        self.write("SRVY0-ACQU0_60m-10Hz.csv", line_csv(count=30))
        _, first = self.run_import([self.entry("SRVY0-ACQU0_60m-10Hz.csv")], "one.geojson")
        self.assertEqual(first["featureCount"], 1)
        (self.sources / "SRVY0-ACQU0_60m-10Hz.csv").unlink()
        _, second = self.run_import(
            [self.entry("SRVY0-ACQU0_60m-10Hz.csv")], "two.geojson", existing=self.root / "one.geojson")
        self.assertEqual(second["featureCount"], 1)
        self.assertEqual(second["features"][0]["id"], first["features"][0]["id"])


if __name__ == "__main__":
    unittest.main()
