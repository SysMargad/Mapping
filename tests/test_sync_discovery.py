"""Drive-side discovery rules: what counts as a coordinate source, and which
campaign is allowed to claim it."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

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
