import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).parents[1] / "scripts" / "extract_geofabrik.py"
SPEC = importlib.util.spec_from_file_location("extract_geofabrik", MODULE)
extract_geofabrik = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extract_geofabrik)


class ExtractGeofabrikTests(unittest.TestCase):
    def test_atomic_json_write_is_complete_public_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restaurants.json"
            payload = {"meta": {"record_count": 1}, "restaurants": [{"id": "n1"}]}

            extract_geofabrik.atomic_write_json(path, payload)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            self.assertEqual(list(path.parent.glob(".restaurants.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
