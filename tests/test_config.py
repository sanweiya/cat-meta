import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catmeta.mihomo import CoreError, CoreManager
from catmeta.paths import APP_DATA
from catmeta.settings import Settings

BASE = {
    "proxies": [{"name": "test", "type": "ss", "server": "127.0.0.1", "port": 1, "cipher": "aes-128-gcm", "password": "x"}],
    "proxy-groups": [{"name": "选择", "type": "select", "proxies": ["test", "DIRECT"]}],
    "rules": ["MATCH,选择"],
}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.tmp.name) / "settings.json")
        self.settings.update(
            {
                "mixed_port": 7981,
                "api_port": 19881,
                "api_secret": "secret",
                "mode": "global",
                "allow_lan": True,
                "ipv6": True,
            }
        )
        self.core = CoreManager(self.settings)

    def tearDown(self):
        self.tmp.cleanup()

    def test_overrides(self):
        config = self.core.build_config_data(BASE)
        self.assertEqual(config["mixed-port"], 7981)
        self.assertEqual(config["external-controller"], "127.0.0.1:19881")
        self.assertEqual(config["secret"], "secret")
        self.assertEqual(config["mode"], "global")
        self.assertTrue(config["allow-lan"])
        self.assertTrue(config["ipv6"])
        self.assertFalse(config["tun"]["enable"])
        self.assertTrue(config["profile"]["store-selected"])

    def test_tun(self):
        self.settings.update({"tun": True, "tun_stack": "gvisor"})
        config = self.core.build_config_data(BASE)
        self.assertTrue(config["tun"]["enable"])
        self.assertEqual(config["tun"]["stack"], "gvisor")
        self.assertTrue(config["dns"]["enable"])

    def test_empty_proxy_config_rejected(self):
        with self.assertRaises(CoreError):
            self.core.build_config_data({"rules": ["MATCH,DIRECT"]})

    def test_settings_persist(self):
        self.settings.set("mixed_port", 7999)
        loaded = Settings(Path(self.tmp.name) / "settings.json")
        self.assertEqual(int(loaded.mixed_port), 7999)
        self.assertTrue(loaded.api_secret)

    def test_builtin_editor_defaults_on(self):
        self.assertTrue(self.settings.use_builtin_editor)
        self.settings.set("use_builtin_editor", False)
        loaded = Settings(Path(self.tmp.name) / "settings.json")
        self.assertFalse(loaded.use_builtin_editor)

    def test_recognizes_own_core_process(self):
        config = str(self.core.runtime_config)
        self.assertTrue(self.core._looks_like_core(["mihomo", "-d", str(APP_DATA), "-f", config]))
        self.assertTrue(self.core._looks_like_core(["mihomo", f"--config={config}"]))
        self.assertFalse(self.core._looks_like_core(["mihomo", "-f", "/tmp/other.yaml"]))
        self.assertFalse(self.core._looks_like_core(["mihomo", "-t", "-f", config]))

    def test_cleanup_kills_stale_process(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            with mock.patch.object(self.core, "_stale_core_pids", return_value=[process.pid]):
                self.core._cleanup_stale_core()
            process.wait(timeout=2)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
