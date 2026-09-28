import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import import_medusa_traverse_tracks as medusa  # noqa: E402


class MedusaTraverseImport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "final_traverse_points.csv"
        self.licences = self.root / "licenses.geojson"
        self.existing = self.root / "tracks.geojson"
        self.output = self.root / "output.geojson"
        self.licences.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"LICENSE": "XV-021395"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [92.48, 46.13], [92.52, 46.13], [92.52, 46.18],
                    [92.48, 46.18], [92.48, 46.13],
                ]]},
            }],
        }), encoding="utf-8")
        self.existing.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "id": "keep", "properties": {}, "geometry": None}],
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self):
        columns = sorted(medusa.REQUIRED_COLUMNS)
        rows = [
            {"session": "20260912T073917", "line_id": "T01", "survey_class": "TRAVERSE",
             "datetime_local_utc8": "2026-09-12 15:00:00+08:00", "gps_Lon": "92.5000",
             "gps_Lat": "46.1400", "lidar_lheight": "25", "hard_qc_pass": "True"},
            {"session": "20260912T073917", "line_id": "T01", "survey_class": "TRAVERSE",
             "datetime_local_utc8": "2026-09-12 15:00:03+08:00", "gps_Lon": "92.5010",
             "gps_Lat": "46.1410", "lidar_lheight": "27", "hard_qc_pass": "True"},
            {"session": "20260912T073917", "line_id": "T02", "survey_class": "TIE",
             "datetime_local_utc8": "2026-09-12 15:01:00+08:00", "gps_Lon": "92.5020",
             "gps_Lat": "46.1420", "lidar_lheight": "28", "hard_qc_pass": "True"},
        ]
        with self.source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def test_build_imports_qc_traverse_without_tracker_id(self):
        self.write_rows()
        summary = medusa.build(self.source, self.licences, self.existing, self.output)
        data = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(summary["featuresImported"], 1)
        self.assertEqual(data["featureCount"], 2)
        feature = next(item for item in data["features"] if item["id"] != "keep")
        self.assertEqual(feature["properties"]["sensor"], "Medusa")
        self.assertEqual(feature["properties"]["date"], "2026-09-12")
        self.assertEqual(feature["properties"]["flightHeightMeanM"], 26.0)
        self.assertEqual(feature["properties"]["coverageStatus"], "not_calculated")
        self.assertNotIn("trackerId", feature["properties"])

    def test_reimport_replaces_the_same_dataset(self):
        self.write_rows()
        medusa.build(self.source, self.licences, self.existing, self.output)
        medusa.build(self.source, self.licences, self.output, self.output)
        data = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(sum(
            feature.get("properties", {}).get("sourceDatasetId") == medusa.DATASET_ID
            for feature in data["features"]
        ), 1)


if __name__ == "__main__":
    unittest.main()
