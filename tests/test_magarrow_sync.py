"""Nergui Undur MagArrow CSV parsing and Drive discovery rules."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys
import json

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import export_magdata_trajectories as magarrow  # noqa: E402
import sync_drive_sources as sync  # noqa: E402


class TenHertzCsv(unittest.TestCase):
    def write_csv(self, body: str) -> Path:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "source.csv"
        path.write_text(body, encoding="utf-8")
        return path

    def test_verified_csv_is_downsampled_to_one_hertz(self):
        path = self.write_csv(
            "Counter,Date,Time,Latitude,Longitude,Mag\n"
            "1,2026/09/21,07:49:57.300,49.12500000,113.54170000,58321\n"
            "2,2026/09/21,07:49:57.400,49.12500001,113.54170001,58320\n"
            "3,2026/09/21,07:49:58.000,49.12510000,113.54180000,58319\n"
            "4,2026/09/21,07:49:59.000,49.12520000,113.54190000,58318\n"
        )
        feature = magarrow.parse_10hz_csv(
            path,
            source_name="SRVY0-ACQU122-10Hz.csv",
            source_url="https://drive.google.com/file/d/example/view",
            source_fingerprint="abc123",
        )
        props = feature["properties"]
        self.assertEqual(feature["id"], "magarrow-actual-srvy0-acqu122")
        self.assertEqual(props["acquisition"], "SRVY0-ACQU122")
        self.assertEqual(props["date"], "2026-09-21")
        self.assertEqual(props["pointCount"], 3)
        self.assertEqual(props["sourceFingerprint"], "abc123")
        self.assertEqual(feature["geometry"]["type"], "LineString")

    def test_duplicate_suffix_remains_a_distinct_acquisition(self):
        path = self.write_csv(
            "Date,Time,Latitude,Longitude\n"
            "2026/09/18,01:00:00.000,49.12,113.54\n"
            "2026/09/18,01:00:01.000,49.13,113.55\n"
        )
        feature = magarrow.parse_10hz_csv(path, source_name="SRVY0-ACQU86 (1)-10Hz.csv")
        self.assertEqual(feature["properties"]["acquisition"], "SRVY0-ACQU86(1)")
        self.assertEqual(feature["id"], "magarrow-actual-srvy0-acqu86-1")

    def test_unrelated_csv_is_rejected(self):
        path = self.write_csv("Date,Time,Mag\n2026/09/21,01:00:00,58321\n")
        with self.assertRaisesRegex(ValueError, "latitude, longitude"):
            magarrow.parse_10hz_csv(path, source_name="SRVY0-ACQU122-10Hz.csv")


class DriveSourceNames(unittest.TestCase):
    def test_converted_csv_and_raw_magdata_are_recognised(self):
        for name in (
            "SRVY0-ACQU70-10Hz.csv",
            "SRVY0-ACQU86 (1)-10Hz.csv",
            "SRVY0-ACQU122.magdata",
        ):
            with self.subTest(name=name):
                self.assertRegex(name, sync.MAGARROW_SOURCE_RE)

    def test_dotted_drive_folder_date_is_understood(self):
        self.assertEqual(sync.source_date_from_path("Raw data/2026.09.21/SRVY0-ACQU122.magdata"), "2026-09-21")


class IncrementalSync(unittest.TestCase):
    def setUp(self):
        self._find = sync.find_nergui_magarrow_sources
        self._download = sync.download_file
        self.addCleanup(lambda: setattr(sync, "find_nergui_magarrow_sources", self._find))
        self.addCleanup(lambda: setattr(sync, "download_file", self._download))

    def test_new_csv_is_imported_and_published(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            workspace = root / "workspace"
            base = repo / "dist" / "data" / "base"
            mag_dir = repo / "dist" / "data" / "magarrow"
            base.mkdir(parents=True)
            mag_dir.mkdir(parents=True)
            polygon = [[
                [113.4, 49.0], [113.7, 49.0], [113.7, 49.3],
                [113.4, 49.3], [113.4, 49.0],
            ]]
            (base / "licence.geojson").write_text(json.dumps({
                "type": "FeatureCollection", "features": [{
                    "type": "Feature", "id": "licence", "properties": {"name": "Licence"},
                    "geometry": {"type": "Polygon", "coordinates": polygon},
                }],
            }), encoding="utf-8")
            (base / "uchastik.geojson").write_text(json.dumps({
                "type": "FeatureCollection", "features": [{
                    "type": "Feature", "id": "U1", "properties": {"name": "U1"},
                    "geometry": {"type": "Polygon", "coordinates": polygon},
                }],
            }), encoding="utf-8")
            (mag_dir / "actual-tracks.geojson").write_text(json.dumps({
                "type": "FeatureCollection", "features": [],
            }), encoding="utf-8")
            (mag_dir / "coverage-stats.json").write_text("{}", encoding="utf-8")
            (repo / "dist" / "data" / "datasets.json").write_text(json.dumps({
                "datasets": [{"id": name} for name in (
                    "magarrow-actual-tracks", "magarrow-coverage-stats", "magarrow-raw",
                )],
            }), encoding="utf-8")

            item = {
                "id": "drive-id", "name": "SRVY0-ACQU122-10Hz.csv", "size": "500",
                "md5Checksum": "checksum", "modifiedTime": "2026-09-22T09:24:52Z",
            }
            sync.find_nergui_magarrow_sources = lambda *_args: [item]

            def download(_service, _item, target):
                target.write_text(
                    "Date,Time,Latitude,Longitude\n"
                    "2026/09/21,07:49:57.000,49.1250,113.5417\n"
                    "2026/09/21,07:49:58.000,49.1251,113.5418\n"
                    "2026/09/21,07:49:59.000,49.1252,113.5419\n",
                    encoding="utf-8",
                )
            sync.download_file = download

            summary = sync.sync_nergui_magarrow(None, "root", repo, workspace, 8, 1024)
            self.assertEqual(summary["importedCount"], 1)
            self.assertEqual(summary["publishedAcquisitionCount"], 1)
            self.assertEqual(summary["dateTo"], "2026-09-21")
            published = json.loads((mag_dir / "actual-tracks.geojson").read_text(encoding="utf-8"))
            self.assertEqual(published["features"][0]["properties"]["acquisition"], "SRVY0-ACQU122")
            self.assertNotIn("driveFileId", json.dumps(published))


if __name__ == "__main__":
    unittest.main()
