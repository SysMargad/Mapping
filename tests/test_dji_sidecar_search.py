"""DJI Timestamp.MRK files are found Drive-wide and matched to tracker rows by name."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import sync_drive_sources as sync  # noqa: E402

try:
    import openpyxl  # noqa: F401
    HAS_OPENPYXL = True
except ImportError:  # the Pages workflow runs the suite with the standard library only
    HAS_OPENPYXL = False


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeFiles:
    def __init__(self, listed, folders):
        self.listed = listed
        self.folders = folders
        self.queries = []

    def list(self, q="", **_kwargs):
        self.queries.append(q)
        return Request({"files": self.listed})

    def get(self, fileId, **_kwargs):
        return Request({"id": fileId, "name": self.folders.get(fileId, "")})


class FakeService:
    def __init__(self, listed, folders=None):
        self._files = FakeFiles(listed, folders or {})

    def files(self):
        return self._files


class SidecarSearch(unittest.TestCase):
    def test_mrk_is_indexed_by_its_mission_name(self):
        service = FakeService([
            {"id": "a", "name": "DJI_202611011528_043_URTIINGOLL3BWAYPOINTDRAFTHOLD_Timestamp.MRK"},
            {"id": "b", "name": "DJI_202609091544_010_Timestamp.MRK"},
            {"id": "c", "name": "Timestamp notes.txt"},
        ])
        index, summary = sync.drive_search_dji_sidecars(service)
        self.assertEqual(summary["sourceCount"], 2)
        # Tracker cells often carry trailing whitespace or a newline.
        key = sync.dji_mission_key("DJI_202611011528_043_URTIINGOLL3BWAYPOINTDRAFTHOLD\n")
        self.assertEqual([item["id"] for item in index[key]], ["a"])
        self.assertIn(sync.dji_mission_key("DJI_202609091544_010"), index)

    def test_copy_in_a_folder_marked_for_deletion_is_not_chosen(self):
        service = FakeService([], {"keep": "DJI_202610081422_005_DEMO", "del": "DJI_202610081422_005_DEMO (delete)"})
        candidates = [
            {"id": "x", "name": "m_Timestamp.MRK", "size": "200", "parents": ["del"], "modifiedTime": "2026-10-09"},
            {"id": "y", "name": "m_Timestamp.MRK", "size": "200", "parents": ["keep"], "modifiedTime": "2026-10-08"},
        ]
        self.assertEqual(sync.choose_dji_sidecar(service, candidates, {})["id"], "y")

    @unittest.skipUnless(HAS_OPENPYXL, "openpyxl is not installed")
    def test_unlinked_tracker_rows_are_available_to_the_search(self):
        from openpyxl import Workbook
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "tracker.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.title = "Checklist"
            sheet.cell(3, 3, "2026-11-01")
            sheet.cell(3, 4, "L3")
            sheet.cell(3, 5, "DJI_202611011528_043_URTIINGOLL3BWAYPOINTDRAFTHOLD")
            book.save(path)
            self.assertEqual(sync.tracker_rows(path, "nergui-undur"), [])
            rows = sync.tracker_rows(path, "nergui-undur", include_unlinked=True)
            self.assertEqual(rows[0]["trackerId"], "nergui-undur:3")
            self.assertIsNone(rows[0]["folderId"])


if __name__ == "__main__":
    unittest.main()
