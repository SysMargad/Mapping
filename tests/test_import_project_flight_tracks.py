"""Tracker-bound flight import: matching rules and unsupported source handling."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import coordinate_sources as cs  # noqa: E402
import import_project_flight_tracks as ipft  # noqa: E402

LON0, LAT0 = 92.35, 46.05

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


def tracker_payload(records):
    return {
        "project": "Multi-project operations",
        "scope": "project_operations",
        "dataType": "flight_register_summary",
        "projects": {},
        "records": records,
    }


def record(row, mission, date, sensor="L2"):
    return {
        "id": f"hetsuu-hutul:{row}", "projectKey": "hetsuu-hutul", "area": "Хэцүү хөтөл",
        "date": date, "sensor": sensor, "mission": mission,
        "dataType": "flight_register_record", "plannedActual": "actual_record",
        "trajectoryAvailable": False,
    }


def mrk_text(count=20):
    lines = []
    for index in range(count):
        lines.append(
            f"{index + 1}\t{index * 0.1:.1f}\t"
            f"{LAT0 + index * 0.00005:.7f},N\t{LON0 + index * 0.00005:.7f},E\t1180.5,Ellh"
        )
    return "\n".join(lines) + "\n"


class ImporterCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.licences = self.root / "licences.geojson"
        self.licences.write_text(json.dumps(LICENCES), encoding="utf-8")
        self.existing = self.root / "existing.geojson"
        self.existing.write_text(json.dumps({
            "type": "FeatureCollection", "project": "Multi-project operations",
            "scope": "project_operations", "dataType": "actual_flight_track", "features": [],
        }), encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def run_import(self, records, mapping=None, output_name="tracks.geojson"):
        trackers = self.root / "trackers.json"
        trackers.write_text(json.dumps(tracker_payload(records), ensure_ascii=False), encoding="utf-8")
        mapping_path = None
        if mapping is not None:
            mapping_path = self.root / "mapping.json"
            mapping_path.write_text(json.dumps({"sources": mapping}, ensure_ascii=False), encoding="utf-8")
        output = self.root / output_name
        args = argparse.Namespace(
            source_dir=self.sources, project="hetsuu-hutul", trackers=trackers,
            licences=self.licences, existing=self.existing, output=output,
            mapping=mapping_path, report=None,
        )
        report = ipft.build(args)
        return report, json.loads(output.read_text(encoding="utf-8"))

    def reasons(self, report, filename):
        return [item["reason"] for item in report["audit"] if item["file"] == filename]


class UnsupportedFlightLogs(unittest.TestCase):
    def test_binary_dji_txt_is_detected(self):
        # Requirement 8.7: a DJIFlightRecord .txt is an encoded container.
        self.assertTrue(ipft.is_binary_flight_log(b"\x55\x2f\x00\x00" + bytes(range(256)) * 4))

    def test_plain_text_table_is_not_flagged_as_binary(self):
        self.assertFalse(ipft.is_binary_flight_log(b"Latitude,Longitude\n46.05,92.35\n"))


class Matching(ImporterCase):
    def test_exact_mission_name_matches(self):
        mission = "DJI_202608221222_001_DEMO-AWarehouse-XT-B007"
        (self.sources / f"{mission}_Timestamp.MRK").write_text(mrk_text(), encoding="utf-8")
        report, data = self.run_import([record(3, mission, "2026-08-22")])
        self.assertEqual(report["imported"], 1)
        self.assertEqual(data["features"][0]["properties"]["trackerId"], "hetsuu-hutul:3")

    def test_two_close_flights_on_one_day_are_ambiguous(self):
        # Requirement 8.4: a same-day time match must not pick a winner when two
        # tracker missions are equally plausible.
        records = [
            record(3, "DJI_202608221222_001_DEMO-AWarehouse-XT-B007", "2026-08-22"),
            record(4, "DJI_202608221223_002_DEMO-AWarehouse-XT-B008", "2026-08-22"),
        ]
        (self.sources / "DJIFlightRecord_2026-08-22_[12-22-30].mrk").write_text(mrk_text(), encoding="utf-8")
        report, data = self.run_import(records)
        self.assertEqual(report["imported"], 0)
        self.assertEqual(data["features"], [])
        self.assertEqual(self.reasons(report, "DJIFlightRecord_2026-08-22_[12-22-30].mrk"),
                         [cs.AMBIGUOUS_MATCH])

    def test_explicit_mapping_resolves_an_otherwise_ambiguous_day(self):
        records = [
            record(3, "DJI_202608221222_001_DEMO-AWarehouse-XT-B007", "2026-08-22"),
            record(4, "DJI_202608221223_002_DEMO-AWarehouse-XT-B008", "2026-08-22"),
        ]
        name = "DJIFlightRecord_2026-08-22_[12-22-30].mrk"
        (self.sources / name).write_text(mrk_text(), encoding="utf-8")
        report, data = self.run_import(records, mapping=[{"file": name, "trackerId": "hetsuu-hutul:4"}])
        self.assertEqual(report["imported"], 1)
        self.assertEqual(data["features"][0]["properties"]["trackerId"], "hetsuu-hutul:4")

    def test_one_source_is_never_used_for_two_missions(self):
        mission = "DJI_202608221222_001_DEMO-AWarehouse-XT-B007"
        (self.sources / f"{mission}_a_Timestamp.MRK").write_text(mrk_text(), encoding="utf-8")
        (self.sources / f"{mission}_b_Timestamp.MRK").write_text(mrk_text(30), encoding="utf-8")
        report, data = self.run_import([record(3, mission, "2026-08-22")])
        self.assertEqual(report["imported"], 1)
        self.assertEqual(len(data["features"]), 1)
        self.assertIn(cs.DUPLICATE_SOURCE, report["reasonCounts"])

    def test_unmatched_source_is_reported_not_guessed(self):
        (self.sources / "random_notes.csv").write_text(
            "Latitude,Longitude\n46.05,92.35\n46.06,92.36\n", encoding="utf-8")
        report, data = self.run_import([record(3, "DJI_202608221222_001_X", "2026-08-22")])
        self.assertEqual(data["features"], [])
        self.assertEqual(self.reasons(report, "random_notes.csv"), [cs.MISSION_UNMATCHED])


class Rejections(ImporterCase):
    def test_binary_dji_txt_reports_unsupported_flight_log(self):
        mission = "DJI_202608221222_001_DEMO-AWarehouse-XT-B007"
        name = f"{mission}.txt"
        (self.sources / name).write_bytes(b"\x55\x2f\x00\x01" + bytes(range(256)) * 8)
        report, data = self.run_import([record(3, mission, "2026-08-22")])
        self.assertEqual(data["features"], [])
        self.assertEqual(self.reasons(report, name), [cs.UNSUPPORTED_FLIGHT_LOG])

    def test_coordinates_from_another_project_are_rejected(self):
        mission = "DJI_202608221222_001_DEMO-AWarehouse-XT-B007"
        name = f"{mission}.csv"
        rows = ["Latitude,Longitude"] + [f"{49.15 + i * 0.0001},{113.50 + i * 0.0001}" for i in range(10)]
        (self.sources / name).write_text("\n".join(rows), encoding="utf-8")
        report, data = self.run_import([record(3, mission, "2026-08-22")])
        self.assertEqual(data["features"], [])
        self.assertEqual(self.reasons(report, name), [cs.WRONG_PROJECT_LOCATION])

    def test_gcp_table_named_after_a_mission_is_not_a_track(self):
        mission = "DJI_202608221222_001_DEMO-AWarehouse-XT-B007"
        name = f"{mission}_gcp.csv"
        rows = ["PointName,Latitude,Longitude", "GCP-1,46.05,92.35", "GCP-2,46.06,92.36"]
        (self.sources / name).write_text("\n".join(rows), encoding="utf-8")
        report, data = self.run_import([record(3, mission, "2026-08-22")])
        self.assertEqual(data["features"], [])
        self.assertEqual(self.reasons(report, name), [cs.INVALID_COORDINATES])

    def test_every_audit_row_uses_a_known_reason_code(self):
        mission = "DJI_202608221222_001_DEMO-AWarehouse-XT-B007"
        (self.sources / f"{mission}_Timestamp.MRK").write_text(mrk_text(), encoding="utf-8")
        (self.sources / "stray.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        report, _ = self.run_import([record(3, mission, "2026-08-22")])
        for item in report["audit"]:
            self.assertIn(item["reason"], cs.REASON_CODES)


class Idempotency(ImporterCase):
    def test_second_run_over_the_same_sources_is_identical(self):
        # Requirement 8.10
        mission = "DJI_202608221222_001_DEMO-AWarehouse-XT-B007"
        (self.sources / f"{mission}_Timestamp.MRK").write_text(mrk_text(), encoding="utf-8")
        records = [record(3, mission, "2026-08-22")]
        _, first = self.run_import(records, output_name="one.geojson")
        # Feed the first output back in, exactly as the daily sync does.
        self.existing.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
        _, second = self.run_import(records, output_name="two.geojson")
        self.assertEqual(len(second["features"]), 1)
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )


if __name__ == "__main__":
    unittest.main()
