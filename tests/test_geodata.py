import tempfile
import unittest
from pathlib import Path

from catmeta.geodata import filenames_for, is_ready, required_geodata, status


class GeodataTests(unittest.TestCase):
    def test_required(self):
        config = {"rules": ["GEOIP,CN,DIRECT", "GEOSITE,cn,DIRECT", "MATCH,选择"]}
        self.assertEqual(required_geodata(config), {"geoip", "geosite"})
        self.assertEqual(required_geodata({"rules": ["MATCH,DIRECT"]}), set())

    def test_filenames(self):
        self.assertEqual(filenames_for({"geoip", "geosite"}), ["geoip.metadb", "geosite.dat"])
        self.assertEqual(filenames_for({"geoip"}, {"geodata-mode": True}), ["geoip.dat"])

    def test_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            self.assertFalse(is_ready("geoip.metadb", data))
            (data / "geoip.metadb").write_bytes(b"\x00" * 1_600_000)
            (data / "geosite.dat").write_bytes(b"\x00" * 600_000)
            self.assertTrue(is_ready("geoip.metadb", data))
            self.assertTrue(is_ready("geosite.dat", data))
            self.assertEqual(status(data), {"geoip": True, "geosite": True})


if __name__ == "__main__":
    unittest.main()
