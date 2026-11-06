"""Nergui Undur MagArrow CSV parsing and Drive discovery rules."""

from __future__ import annotations

import io
import tempfile
import unittest
from unittest import mock
from pathlib import Path
import sys
import json

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import export_magdata_trajectories as magarrow  # noqa: E402
import sync_drive_sources as sync  # noqa: E402


def silence_stdout(test: unittest.TestCase) -> None:
    """Keep fixture ``::warning::`` lines out of real Actions run annotations."""
    patcher = mock.patch("sys.stdout", new_callable=io.StringIO)
    patcher.start()
    test.addCleanup(patcher.stop)


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
        self.assertEqual(feature["id"], "magarrow-actual-2026-09-21-srvy0-acqu122")
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
        self.assertEqual(feature["id"], "magarrow-actual-2026-09-18-srvy0-acqu86-1")

    def test_unrelated_csv_is_rejected(self):
        path = self.write_csv("Date,Time,Mag\n2026/09/21,01:00:00,58321\n")
        with self.assertRaisesRegex(ValueError, "latitude, longitude"):
            magarrow.parse_10hz_csv(path, source_name="SRVY0-ACQU122-10Hz.csv")

    def test_generic_coordinate_csv_is_accepted(self):
        path = self.write_csv(
            "UTC,Lat,Lon\n"
            "2026-10-25 01:00:00,49.1200,113.5400\n"
            "2026-10-25 01:00:01,49.1201,113.5401\n"
            "2026-10-25 01:00:02,49.1202,113.5402\n"
        )
        feature = magarrow.parse_coordinate_table(
            path, source_name="SRVY0-ACQU46.csv", fallback_date="2026-10-25"
        )
        self.assertEqual(feature["properties"]["date"], "2026-10-25")
        self.assertEqual(feature["properties"]["acquisition"], "SRVY0-ACQU46")
        self.assertEqual(feature["id"], "magarrow-actual-2026-10-25-srvy0-acqu46")


class DriveSourceNames(unittest.TestCase):
    def setUp(self):
        silence_stdout(self)

    def test_converted_csv_and_raw_magdata_are_recognised(self):
        for name in (
            "SRVY0-ACQU70-10Hz.csv",
            "SRVY0-ACQU86 (1)-10Hz.csv",
            "SRVY0-ACQU10_10Hz.csv",
            "SRVY0-ACQU234 (1)_10Hz.csv",
            "SRVY0-ACQU122.txt",
            "SRVY0-ACQU122.magdata",
        ):
            with self.subTest(name=name):
                self.assertRegex(name, sync.MAGARROW_SOURCE_RE)

    def test_dotted_drive_folder_date_is_understood(self):
        self.assertEqual(sync.source_date_from_path("Raw data/2026.09.21/SRVY0-ACQU122.magdata"), "2026-09-21")

    def test_reused_acquisition_names_remain_distinct_by_date(self):
        first = sync.magarrow_session_key("2026-08-30", "SRVY0-ACQU27")
        second = sync.magarrow_session_key("2026-10-21", "SRVY0-ACQU27")
        self.assertNotEqual(first, second)
        self.assertEqual(len({first, second}), 2)

    def test_transient_drive_error_is_retried(self):
        class Response:
            status = 500

        class TransientError(Exception):
            resp = Response()

        class Request:
            calls = 0

            def execute(self):
                self.calls += 1
                if self.calls < 3:
                    raise TransientError("temporary")
                return {"files": []}

        request = Request()
        delays = []
        result = sync.execute_with_retry(request, sleep_fn=delays.append)
        self.assertEqual(result, {"files": []})
        self.assertEqual(request.calls, 3)
        self.assertEqual(delays, [1, 2])

    def test_non_transient_drive_error_is_not_retried(self):
        class Response:
            status = 403

        class FatalError(Exception):
            resp = Response()

        class Request:
            calls = 0

            def execute(self):
                self.calls += 1
                raise FatalError("forbidden")

        request = Request()
        with self.assertRaises(FatalError):
            sync.execute_with_retry(request, sleep_fn=lambda _delay: None)
        self.assertEqual(request.calls, 1)

    def test_media_chunks_use_the_drive_retry_budget(self):
        class Downloader:
            retries = None

            def next_chunk(self, *, num_retries):
                self.retries = num_retries
                return "status", True

        downloader = Downloader()
        result = sync.download_next_chunk_with_retry(downloader)
        self.assertEqual(result, ("status", True))
        self.assertEqual(downloader.retries, sync.DRIVE_API_MAX_ATTEMPTS - 1)


class IncrementalSync(unittest.TestCase):
    def setUp(self):
        silence_stdout(self)
        self._find = sync.find_nergui_magarrow_sources
        self._download = sync.download_file
        self._parse_file = magarrow.parse_file
        self._build = magarrow.build
        self.addCleanup(lambda: setattr(sync, "find_nergui_magarrow_sources", self._find))
        self.addCleanup(lambda: setattr(sync, "download_file", self._download))
        self.addCleanup(lambda: setattr(magarrow, "parse_file", self._parse_file))
        self.addCleanup(lambda: setattr(magarrow, "build", self._build))

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
            (base / "licenses.geojson").write_text(json.dumps({
                "type": "FeatureCollection", "features": [{
                    "type": "Feature", "id": "licence", "properties": {"name": "Licence"},
                    "geometry": {"type": "Polygon", "coordinates": polygon},
                }],
            }), encoding="utf-8")
            (base / "uchastics.geojson").write_text(json.dumps({
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

    def test_raw_magdata_staging_does_not_duplicate_final_feature(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            workspace = root / "workspace"
            mag_dir = repo / "dist" / "data" / "magarrow"
            mag_dir.mkdir(parents=True)
            (mag_dir / "actual-tracks.geojson").write_text(json.dumps({
                "type": "FeatureCollection", "features": [],
            }), encoding="utf-8")
            (mag_dir / "coverage-stats.json").write_text("{}", encoding="utf-8")
            datasets = repo / "dist" / "data" / "datasets.json"
            datasets.parent.mkdir(parents=True, exist_ok=True)
            datasets.write_text(json.dumps({
                "datasets": [{"id": name} for name in (
                    "magarrow-actual-tracks", "magarrow-coverage-stats", "magarrow-raw",
                )],
            }), encoding="utf-8")

            item = {
                "id": "raw-drive-id", "name": "SRVY0-ACQU151.magdata", "size": "10",
                "md5Checksum": "checksum", "modifiedTime": "2026-09-22T09:24:52Z",
                "relativePath": "Raw data/2026.09.21/SRVY0-ACQU151.magdata",
            }
            failed_item = {
                "id": "failed-drive-id", "name": "SRVY0-ACQU152.magdata", "size": "10",
                "md5Checksum": "failed", "modifiedTime": "2026-09-22T09:24:53Z",
                "relativePath": "Raw data/2026.09.21/SRVY0-ACQU152.magdata",
            }
            sync.find_nergui_magarrow_sources = lambda *_args: [item, failed_item]
            sync.download_file = lambda _service, _item, target: target.write_bytes(b"raw")

            feature = {
                "type": "Feature", "id": "magarrow-actual-srvy0-acqu151",
                "properties": {
                    "acquisition": "SRVY0-ACQU151", "date": "2026-09-21",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[113.54, 49.12], [113.55, 49.13]],
                },
            }

            def parse_file(_source, output_dir):
                if "ACQU152" in _source.name:
                    raise ValueError("deliberately unreadable fixture")
                self.assertEqual(output_dir.name, "nergui-magarrow-raw-staging")
                target = output_dir / "intermediate.json"
                target.write_text(json.dumps(feature), encoding="utf-8")
                return target

            def build(parsed_dir, _licence, _uchastik, _tracks, _coverage):
                parsed = list(parsed_dir.glob("*.json"))
                self.assertEqual([path.name for path in parsed], ["magarrow-actual-srvy0-acqu151.json"])

            magarrow.parse_file = parse_file
            magarrow.build = build
            summary = sync.sync_nergui_magarrow(None, "root", repo, workspace, 8, 1024)
            self.assertEqual(summary["publishedAcquisitionCount"], 1)
            self.assertEqual(summary["failedCount"], 1)
            self.assertTrue(summary["partialImport"])


NERGUI_POLYGON = [[[113.4, 49.0], [113.7, 49.0], [113.7, 49.3], [113.4, 49.3], [113.4, 49.0]]]
ARTSAT_POLYGON = [[[92.46, 46.13], [92.52, 46.13], [92.52, 46.19], [92.46, 46.19], [92.46, 46.13]]]
OTHER_POLYGON = [[[96.09, 45.21], [96.23, 45.21], [96.23, 45.26], [96.09, 45.26], [96.09, 45.21]]]


def licence_feature(code, name, label, polygon):
    return {
        "type": "Feature", "id": code,
        "properties": {"LICENSE": code, "AREANAME": name, "AREANAME_L": label},
        "geometry": {"type": "Polygon", "coordinates": polygon},
    }


def csv_body(day, start_hms, lat, lon, rows=4):
    hours, minutes, seconds = (int(part) for part in start_hms.split(":"))
    lines = ["Counter,Date,Time,Latitude,Longitude,Mag"]
    for index in range(rows):
        lines.append(
            f"{index},{day},{hours:02d}:{minutes:02d}:{seconds + index:02d}.000,"
            f"{lat + index * 0.0001:.6f},{lon + index * 0.0001:.6f},58000"
        )
    return "\n".join(lines) + "\n"


class AllLicenceMagArrow(unittest.TestCase):
    """MagArrow tracks reach every licence, attributed by their own coordinates."""

    def setUp(self):
        silence_stdout(self)
        self._download = sync.download_file
        self.addCleanup(lambda: setattr(sync, "download_file", self._download))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.workspace = root / "workspace"
        data = self.repo / "dist" / "data"
        for folder in ("base", "magarrow", "context"):
            (data / folder).mkdir(parents=True)
        nergui = licence_feature("XV-023099", "Nergui undur", "Нэргүй өндөр", NERGUI_POLYGON)
        (data / "base" / "licenses.geojson").write_text(json.dumps({
            "type": "FeatureCollection", "features": [nergui],
        }), encoding="utf-8")
        (data / "base" / "uchastics.geojson").write_text(json.dumps({
            "type": "FeatureCollection", "features": [{
                "type": "Feature", "id": "U1", "properties": {"name": "U1"},
                "geometry": {"type": "Polygon", "coordinates": NERGUI_POLYGON},
            }],
        }), encoding="utf-8")
        (data / "context" / "licenses.geojson").write_text(json.dumps({
            "type": "FeatureCollection", "features": [
                nergui,
                licence_feature("XV-021395", "Artsat", "Арцат", ARTSAT_POLYGON),
                licence_feature("XV-022904", "Nudengiin har", "Нүдэнгийн хар", OTHER_POLYGON),
            ],
        }), encoding="utf-8")
        self.existing_nergui = [
            {
                "type": "Feature", "id": "magarrow-actual-2026-10-27-srvy0-acqu58",
                "properties": {
                    "project": "Nergui Undur", "sensor": "MagArrow", "acquisition": "SRVY0-ACQU58",
                    "date": "2026-10-27", "startTime": "2026-10-27T08:48:06Z",
                    "sourceFile": "SRVY0-ACQU58.magdata", "sourceFingerprint": "fp-raw-58",
                    "plannedActual": "actual", "dataType": "actual_flight_track",
                },
                "geometry": {"type": "LineString", "coordinates": [[113.50, 49.10], [113.51, 49.11]]},
            },
            {
                "type": "Feature", "id": "magarrow-actual-2026-10-27-srvy0-acqu58-1",
                "properties": {
                    "project": "Nergui Undur", "sensor": "MagArrow", "acquisition": "SRVY0-ACQU58(1)",
                    "date": "2026-10-27", "startTime": "2026-10-27T08:48:06.300000Z",
                    "sourceFile": "SRVY0-ACQU58 (1)-10Hz.csv", "sourceFingerprint": "fp-csv-58",
                    "plannedActual": "actual", "dataType": "actual_flight_track",
                },
                "geometry": {"type": "LineString", "coordinates": [[113.50, 49.10], [113.52, 49.12]]},
            },
        ]
        (data / "magarrow" / "actual-tracks.geojson").write_text(json.dumps({
            "type": "FeatureCollection", "features": self.existing_nergui,
        }), encoding="utf-8")
        (data / "magarrow" / "coverage-stats.json").write_text("{}", encoding="utf-8")
        self.dji_feature = {
            "type": "Feature", "id": "artsat-12-dji",
            "properties": {
                "project": "Artsat", "projectKey": "artsat", "sensor": "L2", "trackerId": "artsat:12",
                "date": "2026-08-09", "contextOnly": True, "dataType": "actual_flight_track",
            },
            "geometry": {"type": "LineString", "coordinates": [[92.48, 46.15], [92.49, 46.16]]},
        }
        (data / "context" / "project-flight-tracks.geojson").write_text(json.dumps({
            "type": "FeatureCollection", "name": "Verified project flight trajectories",
            "project": "Multi-project operations", "scope": "project_operations",
            "dataType": "actual_flight_track", "featureCount": 1, "features": [self.dji_feature],
        }), encoding="utf-8")
        (data / "datasets.json").write_text(json.dumps({
            "datasets": [{"id": name} for name in (
                "magarrow-actual-tracks", "magarrow-coverage-stats", "magarrow-raw",
            )],
        }), encoding="utf-8")
        self.downloads = []
        self.bodies = {}

        def download(_service, item, target):
            self.downloads.append(item["id"])
            target.write_text(self.bodies[item["id"]], encoding="utf-8")
        sync.download_file = download

    def artsat_sources(self):
        csv_item = {
            "id": "artsat-csv-63", "name": "SRVY0-ACQU63-10Hz.csv", "size": "500",
            "md5Checksum": "a63", "modifiedTime": "2026-11-06T02:05:00Z",
            "relativePath": "2. Geophysics/2. Processed_Data/Converted to CSV/SRVY0-ACQU63-10Hz.csv",
        }
        raw_item = {
            "id": "artsat-raw-63", "name": "SRVY0-ACQU63.magdata", "size": "500",
            "md5Checksum": "r63", "modifiedTime": "2026-11-05T13:53:00Z",
            "relativePath": "1. Raw_Data/MagArrow/Artsat/2026.11.05/SRVY0-ACQU63.magdata",
        }
        underscore_item = {
            "id": "other-csv-1", "name": "SRVY0-ACQU1_10Hz.csv", "size": "500",
            "md5Checksum": "o1", "modifiedTime": "2026-11-07T02:05:00Z",
            "relativePath": "Nudengiin khar/2026-11-07/SRVY0-ACQU1_10Hz.csv",
        }
        self.bodies.update({
            "artsat-csv-63": csv_body("2026/11/05", "04:49:33", 46.15, 92.49),
            "artsat-raw-63": "raw container is never needed",
            "other-csv-1": csv_body("2026/11/07", "02:10:00", 45.23, 96.15),
        })
        return [raw_item, csv_item, underscore_item]

    def run_sync(self, sources):
        return sync.sync_magarrow_tracks(
            None, sources, self.repo, self.workspace, 1024 * 1024,
            [{"key": "artsat", "licence": "XV-021395"}],
        )

    def published(self, name):
        folder = "magarrow" if name == "actual-tracks.geojson" else "context"
        return json.loads((self.repo / "dist" / "data" / folder / name).read_text(encoding="utf-8"))

    def test_artsat_flight_is_published_under_artsat_with_magarrow_sensor(self):
        summary = self.run_sync(self.artsat_sources())
        tracks = self.published("project-flight-tracks.geojson")
        magarrow_tracks = [f for f in tracks["features"] if f["properties"]["sensor"] == "MagArrow"]
        by_project = {f["properties"]["projectKey"]: f for f in magarrow_tracks}
        self.assertEqual(set(by_project), {"artsat", "licence-xv-022904"})
        artsat = by_project["artsat"]["properties"]
        self.assertEqual(artsat["project"], "Artsat")
        self.assertEqual(artsat["licence"], "XV-021395")
        self.assertEqual(artsat["date"], "2026-11-05")
        self.assertEqual(artsat["coverageStatus"], "calculated")
        self.assertTrue(artsat["contextOnly"])
        self.assertNotIn("trackerId", artsat)
        other = by_project["licence-xv-022904"]["properties"]
        self.assertEqual(other["project"], "Nudengiin har")
        self.assertEqual(other["area"], "Нүдэнгийн хар")
        self.assertTrue(other["autoDiscovered"])
        # The tracker-bound DJI trajectory is untouched.
        self.assertIn(self.dji_feature, tracks["features"])
        self.assertEqual(tracks["featureCount"], len(tracks["features"]))
        # The raw container of the same flight is recognised without a download.
        self.assertNotIn("artsat-raw-63", self.downloads)
        self.assertEqual(summary["alternateSourceCount"], 1)
        self.assertEqual(summary["publishedByProject"]["artsat"]["dateTo"], "2026-11-05")
        # Nergui Undur keeps its own layer.
        nergui = self.published("actual-tracks.geojson")
        self.assertTrue(all(f["properties"]["project"] == "Nergui Undur" for f in nergui["features"]))

    def test_second_run_downloads_nothing(self):
        sources = self.artsat_sources()
        self.run_sync(sources)
        self.downloads.clear()
        summary = self.run_sync(sources)
        self.assertEqual(self.downloads, [])
        self.assertEqual(summary["importedCount"], 0)
        self.assertEqual(summary["retainedCount"], len(sources))
        tracks = self.published("project-flight-tracks.geojson")
        self.assertEqual(sum(1 for f in tracks["features"] if f["properties"]["sensor"] == "MagArrow"), 2)

    def test_published_copies_of_one_flight_are_collapsed(self):
        summary = self.run_sync([])
        nergui = self.published("actual-tracks.geojson")["features"]
        self.assertEqual(len(nergui), 1)
        props = nergui[0]["properties"]
        self.assertEqual(summary["duplicateCollapsedCount"], 1)
        self.assertEqual(props["acquisition"], "SRVY0-ACQU58")
        self.assertEqual(nergui[0]["id"], "magarrow-actual-2026-10-27-srvy0-acqu58")
        # The converted CSV is the preferred source; both copies are remembered.
        self.assertEqual(props["sourceFile"], "SRVY0-ACQU58 (1)-10Hz.csv")
        self.assertEqual(set(props["sourceFingerprints"]), {"fp-raw-58", "fp-csv-58"})

    def test_copy_filed_under_another_day_does_not_keep_that_day(self):
        misfiled = {
            "type": "Feature", "id": "magarrow-actual-2026-09-21-srvy0-acqu124",
            "properties": {"acquisition": "SRVY0-ACQU124", "date": "2026-09-21",
                           "startTime": "2026-09-23T02:04:22Z", "sourceFile": "SRVY0-ACQU124.magdata",
                           "sourceFingerprint": "fp-copy"},
            "geometry": {"type": "LineString", "coordinates": [[113.5, 49.1], [113.51, 49.11]]},
        }
        original = json.loads(json.dumps(misfiled))
        original["id"] = "magarrow-actual-2026-09-23-srvy0-acqu124"
        original["properties"].update({"date": "2026-09-23", "sourceFingerprint": "fp-original"})
        kept, collapsed = sync.collapse_magarrow_duplicates([
            {"feature": misfiled, "collection": "nergui"},
            {"feature": original, "collection": "nergui"},
        ])
        self.assertEqual(collapsed, 1)
        self.assertEqual(kept[0]["feature"]["properties"]["date"], "2026-09-23")
        self.assertEqual(set(kept[0]["feature"]["properties"]["sourceFingerprints"]), {"fp-copy", "fp-original"})

    def test_distinct_flights_that_reuse_an_acquisition_name_stay_apart(self):
        left = {"properties": {"acquisition": "SRVY0-ACQU86", "date": "2026-09-18",
                               "startTime": "2026-09-18T00:10:00Z"}}
        right = {"properties": {"acquisition": "SRVY0-ACQU86(1)", "date": "2026-09-18",
                                "startTime": "2026-09-18T08:10:00Z"}}
        self.assertFalse(sync.same_magarrow_flight(left, right))

    def test_track_far_from_every_licence_is_not_published(self):
        self.bodies["far"] = csv_body("2026/11/05", "04:00:00", 50.0, 106.9)
        summary = self.run_sync([{
            "id": "far", "name": "SRVY0-ACQU5-10Hz.csv", "size": "10",
            "md5Checksum": "f", "modifiedTime": "2026-11-05T00:00:00Z",
        }])
        self.assertEqual(summary["unassignedCount"], 1)
        tracks = self.published("project-flight-tracks.geojson")
        self.assertFalse(any(f["properties"]["sensor"] == "MagArrow" for f in tracks["features"]))


class LicenceAttribution(unittest.TestCase):
    def setUp(self):
        self.licences = [
            {"code": "XV-023099", "name": "Nergui undur", "label": "Нэргүй өндөр",
             "geometry": {"type": "Polygon", "coordinates": NERGUI_POLYGON}, "bbox": (113.4, 49.0, 113.7, 49.3)},
            {"code": "XV-021395", "name": "Artsat", "label": "Арцат",
             "geometry": {"type": "Polygon", "coordinates": ARTSAT_POLYGON}, "bbox": (92.46, 46.13, 92.52, 46.19)},
        ]

    def test_mostly_outside_track_keeps_its_licence(self):
        # Tie lines overrun the boundary: 1 of 4 points inside is still Nergui.
        coords = [[113.55, 49.29], [113.55, 49.31], [113.55, 49.32], [113.55, 49.33]]
        self.assertEqual(sync.assign_licence(coords, self.licences)["code"], "XV-023099")

    def test_track_near_but_outside_falls_back_to_nearest_licence(self):
        self.assertEqual(sync.assign_licence([[92.53, 46.15], [92.54, 46.15]], self.licences)["code"], "XV-021395")

    def test_folder_date_is_kept_for_utc_previous_day(self):
        feature = {"id": "x", "properties": {
            "acquisition": "SRVY0-ACQU22", "date": "2026-10-18", "startTime": "2026-10-18T23:05:00Z",
        }}
        self.assertFalse(sync.settle_magarrow_date(feature, "2026-10-19"))
        self.assertEqual(feature["properties"]["date"], "2026-10-19")
        self.assertEqual(feature["id"], "magarrow-actual-2026-10-19-srvy0-acqu22")

    def test_misfiled_folder_date_is_overruled_by_gnss(self):
        feature = {"id": "x", "properties": {
            "acquisition": "SRVY0-ACQU2", "date": "2026-10-15", "startTime": "2026-10-15T03:00:00Z",
        }}
        self.assertTrue(sync.settle_magarrow_date(feature, "2026-10-13"))
        self.assertEqual(feature["properties"]["date"], "2026-10-15")

    def test_deepest_folder_date_wins(self):
        path = "MagArrow_10Hz_20261013-16_Copies/2026-10-15/SRVY0-ACQU3_10Hz.csv"
        self.assertEqual(sync.nearest_path_date(path), "2026-10-15")

    def test_parser_accepts_positions_outside_nergui(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "artsat.csv"
            path.write_text(csv_body("2026/11/05", "04:49:33", 46.15, 92.49), encoding="utf-8")
            feature = magarrow.parse_10hz_csv(path, source_name="SRVY0-ACQU63 (1)-10Hz.csv")
            self.assertEqual(feature["properties"]["date"], "2026-11-05")
            self.assertEqual(feature["geometry"]["coordinates"][0], [92.49, 46.15])


if __name__ == "__main__":
    unittest.main()
