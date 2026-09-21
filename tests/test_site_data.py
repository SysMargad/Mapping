"""Guards on the committed site data and registry.

These run against dist/ exactly as GitHub Pages serves it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "dist"
DATA = DIST / "data"


def load(relative: str):
    return json.loads((DATA / relative).read_text(encoding="utf-8"))


class Registry(unittest.TestCase):
    def setUp(self):
        self.registry = load("datasets.json")
        self.by_id = {item["id"]: item for item in self.registry["datasets"]}

    def test_validate_data_passes(self):
        result = subprocess.run(
            [sys.executable, str(REPO / "tools" / "validate_data.py")],
            capture_output=True, text=True, cwd=REPO,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_campaign_dataset_is_registered(self):
        entry = self.by_id["context-project-campaign-tracks"]
        self.assertEqual(entry["scope"], "project_operations")
        self.assertEqual(entry["dataType"], "survey_campaign_track")
        self.assertEqual(entry["project"], "Multi-project operations")

    def test_every_web_asset_exists_and_is_in_the_manifest(self):
        manifest = load("manifest.json")
        for entry in self.registry["datasets"]:
            asset = entry.get("webAsset")
            if not asset:
                continue
            with self.subTest(dataset=entry["id"]):
                self.assertTrue((DIST / asset.removeprefix("./")).exists())
                self.assertIn(entry["id"], manifest["datasets"])
                self.assertIn(entry["id"], manifest["hashes"])

    def test_app_js_parses(self):
        result = subprocess.run(["node", "--check", str(DIST / "app.js")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ExistingProjectsUnaffected(unittest.TestCase):
    """Requirement 8.11: the other projects and layers keep working."""

    def setUp(self):
        self.tracks = load("context/project-flight-tracks.geojson")
        self.coverage = load("context/project-flight-coverage.json")

    def test_verified_trajectories_are_still_present(self):
        counts = {}
        for feature in self.tracks["features"]:
            key = feature["properties"]["projectKey"]
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(counts, {"buduunkhad": 120, "artsat": 1})

    def test_coverage_still_covers_the_projects_with_trajectories(self):
        self.assertEqual(set(self.coverage["projects"]), {"artsat", "buduunkhad"})

    def test_hetsuu_has_no_fabricated_coverage(self):
        # Requirement 8.9: no trajectory means no coverage entry at all, which
        # the UI renders as "Тооцоогүй" rather than 0 м².
        self.assertNotIn("hetsuu-hutul", self.coverage["projects"])

    def test_plan_and_cad_layers_are_intact(self):
        self.assertGreater(len(load("magarrow/planned-survey.geojson")["features"]), 0)
        self.assertGreater(len(load("magarrow/mission-plans.geojson")["features"]), 0)
        self.assertGreater(len(load("context/hetsuu-hutul-dwg.geojson")["features"]), 0)


class CampaignDataset(unittest.TestCase):
    def setUp(self):
        self.data = load("context/project-campaign-tracks.geojson")

    def test_root_metadata_is_isolated_as_project_operations(self):
        self.assertEqual(self.data["project"], "Multi-project operations")
        self.assertEqual(self.data["scope"], "project_operations")
        self.assertEqual(self.data["dataType"], "survey_campaign_track")

    def test_feature_count_matches_features(self):
        self.assertEqual(self.data["featureCount"], len(self.data["features"]))

    def test_the_hetsuu_2025_magnetic_campaign_is_present(self):
        # The problem this dataset was added to fix: Hetsuu had no GPS track of
        # any kind. These 14 segments come from the two verified 2025-11-03
        # per-acquisition files.
        hetsuu = [f for f in self.data["features"]
                  if f["properties"]["projectKey"] == "hetsuu-hutul"]
        self.assertEqual(len(hetsuu), 14)
        campaigns = {f["properties"]["campaignId"] for f in hetsuu}
        self.assertEqual(campaigns, {"hetsuu-hutul-2025-magnetic"})
        years = {f["properties"]["campaignYear"] for f in hetsuu}
        self.assertEqual(years, {2025})

    def test_campaign_geometry_sits_inside_the_hetsuu_licence(self):
        for feature in self.data["features"]:
            if feature["properties"]["projectKey"] != "hetsuu-hutul":
                continue
            for lon, lat in feature["geometry"]["coordinates"]:
                self.assertTrue(92.2 <= lon <= 92.6, (feature["id"], lon))
                self.assertTrue(45.9 <= lat <= 46.2, (feature["id"], lat))

    def test_campaign_dates_stay_in_2025(self):
        for feature in self.data["features"]:
            start = feature["properties"].get("startTime")
            if start:
                self.assertTrue(start.startswith("2025-"), start)

    def test_no_campaign_feature_borrows_a_tracker_mission(self):
        for feature in self.data["features"]:
            props = feature["properties"]
            with self.subTest(feature=feature["id"]):
                self.assertNotIn("trackerId", props)
                self.assertNotIn("mission", props)
                self.assertIn("campaignId", props)
                self.assertIn(props["coverageStatus"], ("not_calculated", "calculated"))


class TrackerRegister(unittest.TestCase):
    def setUp(self):
        self.trackers = load("context/project-trackers.json")

    def test_hetsuu_register_is_present_without_claimed_trajectories(self):
        hetsuu = self.trackers["projects"]["hetsuu-hutul"]
        self.assertGreater(hetsuu["records"], 0)
        self.assertFalse(hetsuu["trajectoryAvailable"])

    def test_no_tracker_record_claims_to_be_a_trajectory(self):
        self.assertTrue(all(
            record["trajectoryAvailable"] is False for record in self.trackers["records"]
        ))


if __name__ == "__main__":
    unittest.main()
