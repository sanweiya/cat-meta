import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catmeta import polkit


class PolkitRuleTests(unittest.TestCase):
    def test_rule_text_is_scoped_to_resolve1_and_user(self):
        text = polkit.rule_text("cx")
        for action in polkit.ACTIONS:
            self.assertIn(action, text)
        self.assertIn('subject.user === "cx"', text)
        self.assertIn("subject.local", text)
        # 只放行 resolve1 的四个动作，不涉及其它 polkit 授权。
        self.assertNotIn("org.freedesktop.systemd1", text)
        self.assertNotIn("org.freedesktop.login1", text)

    def test_rule_installed_detects_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "49-cat-meta-tun.rules"
            with mock.patch.object(polkit, "RULE_PATH", path):
                with mock.patch.object(polkit, "_authorized", return_value=False):
                    self.assertFalse(polkit.rule_installed())
                path.write_text(polkit.rule_text("cx"), encoding="utf-8")
                self.assertTrue(polkit.rule_installed())

    def test_rule_installed_falls_back_to_pkcheck(self):
        # Fedora 的 /etc/polkit-1/rules.d 是 0700 root，读不到文件时要问 polkit。
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.rules"
            with mock.patch.object(polkit, "RULE_PATH", missing):
                with mock.patch.object(polkit, "_authorized", return_value=True):
                    self.assertTrue(polkit.rule_installed())
                with mock.patch.object(polkit, "_authorized", return_value=False):
                    self.assertFalse(polkit.rule_installed())
