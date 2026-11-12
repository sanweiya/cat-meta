import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catmeta.profiles import (
    ProfileError,
    ProfileStore,
    SAMPLE_PROFILE,
    parse_subscription,
    validate_config_text,
)

SIMPLE = """\
proxies:
  - name: test
    type: ss
    server: 127.0.0.1
    port: 1
    cipher: aes-128-gcm
    password: test
proxy-groups:
  - name: 选择
    type: select
    proxies: [test, DIRECT]
rules:
  - MATCH,选择
"""


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.profiles_dir = self.root / "profiles"
        self.profiles_dir.mkdir()
        self.profiles_file = self.root / "profiles.json"
        self.patches = [
            patch("catmeta.profiles.PROFILES_DIR", self.profiles_dir),
            patch("catmeta.profiles.PROFILES_FILE", self.profiles_file),
        ]
        for item in self.patches:
            item.start()
        self.store = ProfileStore(self.profiles_file)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_sample_installed(self):
        self.assertEqual(len(self.store.all()), 1)
        self.assertIsNotNone(self.store.current())
        self.assertIn("proxy-groups", self.store.content())

    def test_add_manual_and_current(self):
        profile = self.store.add_manual("测试", SIMPLE)
        self.assertNotEqual(self.store.current_id, profile.id)
        self.store.set_current(profile.id)
        self.assertEqual(self.store.current().name, "测试")
        stats = self.store.stats(profile.id)
        self.assertEqual(stats["proxies"], 1)
        self.assertEqual(stats["rules"], 1)

    def test_invalid_content_rejected(self):
        with self.assertRaises(ProfileError):
            self.store.add_manual("bad", "foo: bar")
        with self.assertRaises(ProfileError):
            validate_config_text("- a\n- b")

    def test_remote_import_and_update(self):
        headers = {"subscription-userinfo": "upload=10; download=20; total=100; expire=2000000000", "profile-title": "远程测试"}
        with patch("catmeta.profiles.fetch_text", return_value=(SIMPLE, headers)):
            profile = self.store.add_remote("https://example.com/sub")
        self.assertEqual(profile.name, "远程测试")
        self.assertEqual(profile.kind, "remote")
        self.assertEqual(profile.subscription["total"], 100)
        self.assertNotEqual(self.store.current_id, profile.id)
        self.store.set_current(profile.id)

        headers2 = {"subscription-userinfo": "upload=50; download=50; total=100; expire=2000000000"}
        with patch("catmeta.profiles.fetch_text", return_value=(SIMPLE, headers2)):
            updated = self.store.update(profile.id)
        self.assertEqual(updated.subscription["upload"], 50)

    def test_rename_and_delete(self):
        profile = self.store.add_manual("旧名称", SIMPLE)
        self.store.rename(profile.id, "新名称")
        self.assertEqual(self.store.get(profile.id).name, "新名称")
        self.store.delete(profile.id)
        self.assertIsNone(self.store.get(profile.id))
        self.assertFalse(profile.path.exists())

    def test_parse_subscription(self):
        result = parse_subscription({"subscription-userinfo": "upload=1; download=2; total=3; expire=4"})
        self.assertEqual(result, {"upload": 1, "download": 2, "total": 3, "expire": 4})

    def test_sample_is_valid(self):
        data = validate_config_text(SAMPLE_PROFILE)
        self.assertIn("rules", data)


if __name__ == "__main__":
    unittest.main()
