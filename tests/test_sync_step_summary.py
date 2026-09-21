"""The Actions run summary must report stage counts without leaking Drive paths."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import sync_drive_sources as sync  # noqa: E402

SUMMARY = {
    "secretsConfigured": {
        "HETSUU_HUTUL_ROOT_FOLDER_ID": False,
        "NERGUI_UNDUR_RAW_ROOT_FOLDER_ID": True,
    },
    "hetsuuHutulDroneSources": {
        "matchedBranchCount": 1,
        "scannedFolderCount": 39,
        "inspectedFileCount": 214,
        "headerProbesUsed": 96,
        "coordinateSourceCount": 74,
        "sourceCountByType": {"csv": 74},
        "discoveryDecisions": {"name-pattern": 40, "probe-header": 34, "probe-point-table": 6},
        "dateFrom": "2025-09-22",
        "dateTo": "2025-11-03",
    },
    "projects": {
        "hetsuu-hutul": {
            "trackerMissionRows": 138,
            "stages": {
                "missionFoldersScanned": 0,
                "missionFoldersUnreadable": 138,
                "candidateFilesFound": 0,
                "sourcesChosen": 0,
                "sourcesDownloaded": 2,
                "downloadFailures": 0,
            },
            "reasons": {"MISSING_SOURCE": 138},
            "importReport": {"imported": 0, "reasonCounts": {"MISSION_UNMATCHED": 1}},
            "campaignImportReport": {"segments": 14, "reasonCounts": {"IMPORTED": 2, "DUPLICATE_SOURCE": 1}},
        },
        "artsat": {
            "trackerMissionRows": 60,
            "stages": {"missionFoldersScanned": 60, "missionFoldersUnreadable": 0,
                       "candidateFilesFound": 12, "sourcesChosen": 1,
                       "sourcesDownloaded": 1, "downloadFailures": 0},
            "reasons": {},
            "importReport": {"imported": 1, "reasonCounts": {"IMPORTED": 1}},
        },
    },
}


class StepSummary(unittest.TestCase):
    def setUp(self):
        self.text = sync.step_summary(SUMMARY)

    def test_missing_secret_is_called_out(self):
        self.assertIn("`HETSUU_HUTUL_ROOT_FOLDER_ID` | **no**", self.text)
        self.assertIn("`NERGUI_UNDUR_RAW_ROOT_FOLDER_ID` | yes", self.text)

    def test_discovery_counters_are_reported(self):
        for token in ("scannedFolderCount", "inspectedFileCount", "headerProbesUsed",
                      "coordinateSourceCount", "discoveryDecisions"):
            self.assertIn(token, self.text)

    def test_unreadable_folders_are_visible_as_an_access_problem(self):
        # 138 unreadable mission folders is the signature of a sharing gap,
        # and it must be readable straight off the run summary.
        self.assertRegex(self.text, r"\| hetsuu-hutul \| 138 \| 0 \| 138 \|")

    def test_reason_codes_are_merged_per_project(self):
        self.assertIn("MISSING_SOURCE", self.text)
        self.assertIn("DUPLICATE_SOURCE", self.text)
        self.assertIn("IMPORTED", self.text)

    def test_campaign_segments_are_reported_separately_from_tracker_tracks(self):
        self.assertIn("Tracker tracks | Campaign segments", self.text)

    def test_no_secret_value_or_drive_path_is_written(self):
        for forbidden in ("1LS0LCDQ", "drive.google.com", "folders/", "Raw_Data",
                          "Magnetic_Survey", "Geological Data Acquisition", ".csv"):
            self.assertNotIn(forbidden, self.text)

    def test_it_runs_on_an_empty_summary(self):
        text = sync.step_summary({})
        self.assertIn("Drive sync", text)
        self.assertIn("not run", text)


if __name__ == "__main__":
    unittest.main()
