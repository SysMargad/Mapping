import json
import tempfile
import unittest
from pathlib import Path

from tools.sync_local_drive_sources import discover_sources, load_project_roots, normalise


class LocalDriveDiscoveryTests(unittest.TestCase):
    def test_loads_all_configured_project_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({"projects": [
                {"key": "hetsuu-hutul", "rootFolderId": "a"},
                {"key": "artsat", "rootFolderId": "b"},
                {"key": "buduunkhad", "rootFolderId": "c"},
                {"key": "nergui-undur", "rootFolderId": "d"},
            ]}), encoding="utf-8")
            roots = load_project_roots(config, root / "drive")
            self.assertEqual(root / "drive" / "d", roots["nergui-undur"])

    def test_prefers_coordinate_metadata_over_mrk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mission = "DJI_202611011528_042_URTIINGOLL3CWAYPOINTDRAFTHOLD"
            folder = root / mission
            folder.mkdir()
            (folder / f"{mission}_Timestamp.MRK").write_text(
                "1 49.0,Lat 113.0,Lon\n2 49.1,Lat 113.1,Lon\n", encoding="utf-8"
            )
            (folder / "metadata.csv").write_text(
                "latitude,longitude\n49.0,113.0\n49.1,113.1\n", encoding="utf-8"
            )
            mission_key = normalise(mission)
            sources, stats = discover_sources(root, {mission_key: {"mission": mission}})
            self.assertEqual("metadata.csv", sources[mission_key].name)
            self.assertEqual(1, stats["matchedMissions"])

    def test_falls_back_to_mrk_when_metadata_has_no_coordinates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mission = "DJI_202611031441_037_ZHTR01L3CWAYPOINTDRAFTHOLD"
            folder = root / mission
            folder.mkdir()
            mrk = folder / f"{mission}_Timestamp.MRK"
            mrk.write_text("1 49.0,Lat 113.0,Lon\n2 49.1,Lat 113.1,Lon\n", encoding="utf-8")
            (folder / "metadata.csv").write_text("filename,height\na.jpg,20\n", encoding="utf-8")
            mission_key = normalise(mission)
            sources, _stats = discover_sources(root, {mission_key: {"mission": mission}})
            self.assertEqual(mrk, sources[mission_key])


if __name__ == "__main__":
    unittest.main()
