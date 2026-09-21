"""This repository is public, so its Actions logs are public.

Nothing printed by the sync or the importers during a CI run may name a Drive
file or folder that was inspected but not published.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import sync_drive_sources as sync  # noqa: E402

SECRET_NAMES = ("SRVY0-ACQU9_SECRET.csv", "private_survey_notes.csv", "0001__1AbCdEf__hidden.MRK")


class RedactReport(unittest.TestCase):
    def test_file_names_are_replaced_by_counts(self):
        report = {
            "project": "hetsuu-hutul",
            "imported": 1,
            "reasonCounts": {"IMPORTED": 1, "MISSION_UNMATCHED": 2},
            "unmatched": [SECRET_NAMES[0], SECRET_NAMES[1]],
            "rejected": [{"file": SECRET_NAMES[2], "reason": "x"}],
            "audit": [{"file": SECRET_NAMES[0], "reason": "MISSION_UNMATCHED"}],
        }
        redacted = sync.redact_report(report)
        text = json.dumps(redacted, ensure_ascii=False)
        for name in SECRET_NAMES:
            self.assertNotIn(name, text)
        self.assertEqual(redacted["unmatchedCount"], 2)
        self.assertEqual(redacted["rejectedCount"], 1)
        self.assertEqual(redacted["reasonCounts"], {"IMPORTED": 1, "MISSION_UNMATCHED": 2})

    def test_counting_fields_survive(self):
        redacted = sync.redact_report({"project": "artsat", "segments": 14, "importedSources": 2})
        self.assertEqual(redacted, {"project": "artsat", "segments": 14, "importedSources": 2})


class VerboseAuditDefault(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("SYNC_VERBOSE_AUDIT")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("SYNC_VERBOSE_AUDIT", None)
        else:
            os.environ["SYNC_VERBOSE_AUDIT"] = self._saved

    def test_audit_printing_is_off_unless_asked_for(self):
        os.environ.pop("SYNC_VERBOSE_AUDIT", None)
        self.assertFalse(sync.verbose_audit())
        for value in ("1", "true", "YES"):
            os.environ["SYNC_VERBOSE_AUDIT"] = value
            self.assertTrue(sync.verbose_audit(), value)
        os.environ["SYNC_VERBOSE_AUDIT"] = "0"
        self.assertFalse(sync.verbose_audit())

    def test_the_workflow_does_not_enable_verbose_audit(self):
        workflow = (REPO / ".github" / "workflows" / "drive-sync.yml").read_text(encoding="utf-8")
        self.assertNotIn("SYNC_VERBOSE_AUDIT", workflow)


class ImporterStdout(unittest.TestCase):
    """The tracker importer prints to the CI log, so it must print counts only."""

    def test_unmatched_file_names_are_not_printed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            sources = root / "sources"
            sources.mkdir()
            (sources / SECRET_NAMES[1]).write_text(
                "Latitude,Longitude\n46.05,92.35\n46.06,92.36\n", encoding="utf-8")
            licences = root / "lic.geojson"
            licences.write_text(json.dumps({"type": "FeatureCollection", "features": [{
                "type": "Feature", "properties": {"LICENSE": "XV-022905"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [92.28, 46.0], [92.51, 46.0], [92.51, 46.13], [92.28, 46.13], [92.28, 46.0]]]},
            }]}), encoding="utf-8")
            trackers = root / "trackers.json"
            trackers.write_text(json.dumps({
                "project": "Multi-project operations", "scope": "project_operations",
                "dataType": "flight_register_summary", "projects": {}, "records": [],
            }), encoding="utf-8")
            existing = root / "existing.geojson"
            existing.write_text(json.dumps({
                "type": "FeatureCollection", "project": "Multi-project operations",
                "scope": "project_operations", "dataType": "actual_flight_track", "features": [],
            }), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(REPO / "tools" / "import_project_flight_tracks.py"),
                str(sources), "--project", "hetsuu-hutul",
                "--trackers", str(trackers), "--licences", str(licences),
                "--existing", str(existing), "--output", str(root / "out.geojson"),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(SECRET_NAMES[1], result.stdout)
            self.assertIn("unmatchedCount", result.stdout)


if __name__ == "__main__":
    unittest.main()
