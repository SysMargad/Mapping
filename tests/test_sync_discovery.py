"""Drive-side discovery rules: what counts as a coordinate source, and which
campaign is allowed to claim it."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import sync_drive_sources as sync  # noqa: E402

CONFIG = json.loads((REPO / "drive-sync-sources.json").read_text(encoding="utf-8"))
CAMPAIGNS = CONFIG["campaigns"]
LATLONG_CSV = "Latitude,Longitude,Date,Time\n46.05,92.35,2025-11-03,09:00:00\n"


class FakeService:
    """Stands in for the Drive client; serves a fixed first chunk per file id."""

    def __init__(self, contents):
        self.contents = contents
        self.downloads = []


def fake_probe(contents):
    def probe(_service, item):
        return contents.get(item["id"])
    return probe


class NameBasedDiscovery(unittest.TestCase):
    def test_known_flight_formats_match_by_name(self):
        for name in (
            "DJI_202608221222_001_XT-B007_Timestamp.MRK",
            "DJIFlightRecord_2026-08-22_[12-22-30].kmz",
            "SRVY0-ACQU0_60m-10Hz.csv",
        ):
            with self.subTest(name=name):
                self.assertTrue(sync.is_named_drone_source(name))

    def test_plain_coordinate_csv_does_not_match_by_name(self):
        # This is the gap that hid the 2025 magnetic tracks: the name carries no
        # DJI/SRVY token, so name matching alone skipped the file entirely.
        self.assertFalse(sync.is_named_drone_source("1103_latlong.csv"))
        self.assertFalse(sync.is_named_drone_source("1102-1103_all.csv"))

    def test_unsupported_extension_never_matches(self):
        self.assertFalse(sync.is_named_drone_source("report.docx"))


class HeaderProbeDiscovery(unittest.TestCase):
    def setUp(self):
        self._real = sync.probe_header_text
        self.addCleanup(lambda: setattr(sync, "probe_header_text", self._real))

    def classify(self, name, text, budget=10):
        sync.probe_header_text = fake_probe({"id1": text})
        return sync.classify_candidate(None, {"id": "id1", "name": name}, [budget])

    def test_latlong_csv_is_kept_after_reading_its_header(self):
        keep, how = self.classify("1103_latlong.csv", LATLONG_CSV)
        self.assertTrue(keep)
        self.assertEqual(how, "probe-header")

    def test_named_source_skips_the_probe(self):
        keep, how = self.classify("SRVY0-ACQU0_60m-10Hz.csv", LATLONG_CSV)
        self.assertTrue(keep)
        self.assertEqual(how, "name-pattern")

    def test_medusa_zip_is_kept_without_header_probe(self):
        keep, how = self.classify("MS-700.0050_2026_09_25_Artsat.zip", None)
        self.assertTrue(keep)
        self.assertEqual(how, "medusa-archive-name")

    def test_gcp_table_is_rejected_by_the_probe(self):
        keep, how = self.classify("points.csv", "PointName,Latitude,Longitude\nGCP-1,46.05,92.35\n")
        self.assertFalse(keep)
        self.assertEqual(how, "probe-point-table")

    def test_csv_without_coordinates_is_rejected(self):
        keep, how = self.classify("field.csv", "time,field_nT,quality\n1,52000,9\n")
        self.assertFalse(keep)
        self.assertEqual(how, "probe-no-coordinates")

    def test_unreadable_file_is_reported_not_kept(self):
        keep, how = self.classify("broken.csv", None)
        self.assertFalse(keep)
        self.assertEqual(how, "probe-unreadable")

    def test_probe_budget_is_respected(self):
        keep, how = self.classify("1103_latlong.csv", LATLONG_CSV, budget=0)
        self.assertFalse(keep)
        self.assertEqual(how, "probe-budget-exhausted")

    def test_budget_is_only_spent_on_unrecognised_names(self):
        sync.probe_header_text = fake_probe({"id1": LATLONG_CSV})
        budget = [5]
        sync.classify_candidate(None, {"id": "id1", "name": "a_Timestamp.MRK"}, budget)
        self.assertEqual(budget[0], 5)
        sync.classify_candidate(None, {"id": "id1", "name": "1103_latlong.csv"}, budget)
        self.assertEqual(budget[0], 4)


class RawDataDiscovery(unittest.TestCase):
    def test_dji_metadata_name_is_case_insensitive(self):
        self.assertEqual(Path("MetaData.CSV").name.lower(), sync.DJI_METADATA_FILE)

    def test_dji_mission_folder_is_recognised(self):
        self.assertRegex("DJI_202608221541_018_DEMO", sync.DJI_MISSION_FOLDER_RE)
        self.assertNotRegex("Processed_Data", sync.DJI_MISSION_FOLDER_RE)

    def test_numbered_raw_data_folder_is_recognised(self):
        self.assertTrue(sync.is_raw_data_folder("1.Raw data"))
        self.assertTrue(sync.is_raw_data_folder("0 Raw_Data"))
        self.assertFalse(sync.is_raw_data_folder("2. Processed"))

    def test_linked_mission_prefers_existing_metadata_csv(self):
        original_children = sync.list_children
        original_resolve = sync.resolve_shortcut
        self.addCleanup(lambda: setattr(sync, "list_children", original_children))
        self.addCleanup(lambda: setattr(sync, "resolve_shortcut", original_resolve))
        sync.list_children = lambda *_: [{
            "id": "metadata-id", "name": "metadata.csv", "mimeType": "text/csv"
        }, {
            "id": "image-id", "name": "DJI_20260823100001_0001.JPG", "mimeType": "image/jpeg",
            "imageMediaMetadata": {"location": {"latitude": 46.1, "longitude": 92.3}},
        }]
        sync.resolve_shortcut = lambda _service, item: item
        sources = sync.find_sources(None, "mission-folder-id", 2)
        self.assertEqual([item["id"] for item in sources], ["metadata-id"])

    def test_linked_mission_builds_csv_from_drive_image_gps_metadata(self):
        original_children = sync.list_children
        original_resolve = sync.resolve_shortcut
        self.addCleanup(lambda: setattr(sync, "list_children", original_children))
        self.addCleanup(lambda: setattr(sync, "resolve_shortcut", original_resolve))
        sync.list_children = lambda _service, parent: [
            {
                "id": "image-2", "name": "DJI_20260823100002_0002.JPG", "mimeType": "image/jpeg",
                "imageMediaMetadata": {
                    "time": "2026:08:23 10:00:02",
                    "location": {"latitude": 46.2, "longitude": 92.4, "altitude": 1012},
                },
            },
            {
                "id": "image-1", "name": "DJI_20260823100001_0001.JPG", "mimeType": "image/jpeg",
                "imageMediaMetadata": {
                    "time": "2026:08:23 10:00:01",
                    "location": {"latitude": 46.1, "longitude": 92.3, "altitude": 1011},
                },
            },
        ] if parent == "mission-folder-id" else []
        sync.resolve_shortcut = lambda _service, item: item

        sources = sync.find_sources(None, "mission-folder-id", 2)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["name"], sync.DJI_IMAGE_METADATA_FILE)
        self.assertNotIn("mission-folder-id", sources[0]["id"])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / sources[0]["name"]
            sync.download_file(None, sources[0], target)
            parsed = sync.cs.parse_table(target.read_text(encoding="utf-8"))
        self.assertEqual(len(parsed.points), 2)
        self.assertEqual(parsed.points[0].timestamp.isoformat(), "2026-08-23T10:00:01")
        self.assertEqual((parsed.points[0].lat, parsed.points[0].lon), (46.1, 92.3))

    def test_image_metadata_without_two_gps_points_is_not_a_track(self):
        original_children = sync.list_children
        original_resolve = sync.resolve_shortcut
        self.addCleanup(lambda: setattr(sync, "list_children", original_children))
        self.addCleanup(lambda: setattr(sync, "resolve_shortcut", original_resolve))
        sync.list_children = lambda *_: [{
            "id": "image-1", "name": "DJI_20260823100001_0001.JPG", "mimeType": "image/jpeg",
            "imageMediaMetadata": {"location": {"latitude": 46.1, "longitude": 92.3}},
        }]
        sync.resolve_shortcut = lambda _service, item: item
        self.assertEqual(sync.find_sources(None, "mission-folder-id", 2), [])


class FlightRecordDecoding(unittest.TestCase):
    def test_flight_record_matches_nearest_same_sensor_tracker_mission(self):
        candidate = {
            "name": "DJIFlightRecord_2026-10-14_[14-38-05].txt",
            "relativePath": "1.Raw data/October.2026/14/DJI_Zenmuse_L2_LiDAR_Survey_Flight/Metadata/log.txt",
        }
        rows = [
            {"trackerId": "hetsuu-hutul:1", "mission": "DJI_202610141400_005_XT-B139", "sensor": "L2"},
            {"trackerId": "hetsuu-hutul:2", "mission": "DJI_202610141437_006_XT-B139", "sensor": "L2"},
            {"trackerId": "hetsuu-hutul:3", "mission": "DJI_202610141438_007_XT-B139", "sensor": "P1"},
        ]
        matched = sync.match_tracker_by_flight_time(candidate, rows, set())
        self.assertEqual(matched["trackerId"], "hetsuu-hutul:2")

    def test_used_tracker_is_not_reused_for_another_log(self):
        candidate = {"name": "DJIFlightRecord_2026-10-14_[14-38-05].txt", "relativePath": "L2/Metadata"}
        rows = [{"trackerId": "hetsuu-hutul:2", "mission": "DJI_202610141437_006", "sensor": "L2"}]
        self.assertIsNone(sync.match_tracker_by_flight_time(candidate, rows, {"hetsuu-hutul:2"}))

    def test_decoded_frames_are_filtered_sampled_and_written(self):
        frames = [
            SimpleNamespace(osd=SimpleNamespace(latitude=0, longitude=0, altitude=0)),
            *[
                SimpleNamespace(osd=SimpleNamespace(latitude=46.0 + index / 1000, longitude=92.0, altitude=80))
                for index in range(12)
            ],
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "decoded.csv"
            count = sync.write_dji_frames_csv(frames, "flight.txt", target, stride=5)
            parsed = sync.cs.parse_table(target.read_text(encoding="utf-8"))
        self.assertEqual(count, 4)
        self.assertEqual(len(parsed.points), 4)
        self.assertEqual(parsed.columns.relative_alt_name, "RelativeAltitude")


class CampaignClaiming(unittest.TestCase):
    def claim(self, name, path=None):
        candidate = {"name": name, "relativePath": path or f"Magnetic survey/2025/{name}"}
        return sync.match_campaign(CAMPAIGNS, "hetsuu-hutul", candidate)

    def test_declared_magnetic_files_are_claimed(self):
        for name in ("1103_latlong.csv", "1102-1103_all.csv",
                     "SRVY0-ACQU0_60m-10Hz.csv", "SRVY0-ACQU1_40m-10Hz.csv"):
            with self.subTest(name=name):
                campaign = self.claim(name)
                self.assertIsNotNone(campaign, f"{name} should be claimed")
                self.assertEqual(campaign["id"], "hetsuu-hutul-2025-magnetic")

    def test_undeclared_file_is_not_claimed(self):
        self.assertIsNone(self.claim("random_export.csv"))

    def test_campaigns_do_not_cross_projects(self):
        candidate = {"name": "1103_latlong.csv", "relativePath": "Magnetic survey/1103_latlong.csv"}
        self.assertIsNone(sync.match_campaign(CAMPAIGNS, "buduunkhad", candidate))
        self.assertIsNone(sync.match_campaign(CAMPAIGNS, "artsat", candidate))

    def test_campaign_declares_no_tracker_linkage(self):
        campaign = self.claim("1103_latlong.csv")
        for forbidden in ("trackerId", "mission", "sensorFromTracker"):
            self.assertNotIn(forbidden, campaign)

    def test_campaign_does_not_claim_verified_coverage_or_sensor(self):
        campaign = self.claim("1103_latlong.csv")
        self.assertEqual(campaign["coverageStatus"], "not_calculated")
        self.assertFalse(campaign["sensorVerified"])
        self.assertEqual(campaign["gnssSource"], "unverified")

    def test_artsat_medusa_archive_is_claimed_only_inside_medusa_branch(self):
        candidate = {
            "name": "MS-700.0050_2026_09_25_Artsat.zip",
            "relativePath": "2. Geophysics/1. Raw_Data/Medusa/September.2026/25/MS-700.0050_2026_09_25_Artsat.zip",
        }
        campaign = sync.match_campaign(CAMPAIGNS, "artsat", candidate)
        self.assertIsNotNone(campaign)
        self.assertEqual(campaign["id"], "artsat-2026-medusa-ms700")
        candidate["relativePath"] = "Random/MS-700.0050_2026_09_25_Artsat.zip"
        self.assertIsNone(sync.match_campaign(CAMPAIGNS, "artsat", candidate))


class ConfigShape(unittest.TestCase):
    def test_every_campaign_targets_a_known_project(self):
        keys = {project["key"] for project in CONFIG["projects"]}
        for campaign in CAMPAIGNS:
            self.assertIn(campaign["projectKey"], keys)

    def test_every_campaign_has_a_match_rule(self):
        for campaign in CAMPAIGNS:
            rules = campaign.get("match", {})
            self.assertTrue(rules.get("fileNamePatterns") or rules.get("pathContains"))


if __name__ == "__main__":
    unittest.main()
