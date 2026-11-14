import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catmeta import system
from catmeta.system import SystemProxy


class SystemProxyBackendTests(unittest.TestCase):
    def _proxy(self, desktop: str, schemas: str = "org.gnome.system.proxy\n") -> SystemProxy:
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": desktop}, clear=False):
            with mock.patch("catmeta.system.shutil.which", return_value="/usr/bin/gsettings"):
                with mock.patch("catmeta.system._run_gsettings", return_value=(True, schemas)):
                    proxy = SystemProxy(7900)
                    proxy.backend = SystemProxy.backend.__get__(proxy)
                    return proxy

    def _backend(self, desktop: str) -> str:
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": desktop}, clear=False):
            with mock.patch("catmeta.system.shutil.which", return_value="/usr/bin/gsettings"):
                with mock.patch("catmeta.system._run_gsettings", return_value=(True, "org.gnome.system.proxy\n")):
                    return SystemProxy().backend

    def test_xfce_uses_env_backend(self):
        self.assertEqual(self._backend("XFCE"), "env")
        # 环境变量后端在任何桌面都可用，界面不应把开关置灰。
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE"}, clear=False):
            with mock.patch("catmeta.system.shutil.which", return_value="/usr/bin/gsettings"):
                with mock.patch("catmeta.system._run_gsettings", return_value=(True, "")):
                    self.assertTrue(SystemProxy().available)

    def test_lxqt_uses_env_backend(self):
        self.assertEqual(self._backend("LXQt"), "env")

    def test_cinnamon_uses_gsettings(self):
        self.assertEqual(self._backend("X-Cinnamon"), "gsettings")

    def test_gnome_uses_gsettings(self):
        self.assertEqual(self._backend("GNOME"), "gsettings")

    def test_missing_schema_falls_back_to_env(self):
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "X-Cinnamon"}, clear=False):
            with mock.patch("catmeta.system.shutil.which", return_value="/usr/bin/gsettings"):
                with mock.patch("catmeta.system._run_gsettings", return_value=(True, "org.gnome.desktop.interface\n")):
                    self.assertEqual(SystemProxy().backend, "env")


class EnvProxyBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "environment.d" / "90-cat-meta-proxy.conf"

    def _proxy(self) -> SystemProxy:
        proxy = SystemProxy(7900)
        proxy.env_file = self.file
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE"}, clear=False):
            with mock.patch("catmeta.system.shutil.which", return_value="/usr/bin/gsettings"):
                with mock.patch("catmeta.system._run_gsettings", return_value=(True, "")):
                    self.assertEqual(proxy.backend, "env")
        return proxy

    def test_apply_writes_env_file_and_publishes(self):
        proxy = self._proxy()
        with mock.patch("catmeta.system._publish_session_env") as publish:
            ok, message = proxy.apply()
        self.assertTrue(ok, message)
        text = self.file.read_text(encoding="utf-8")
        self.assertIn("http_proxy=http://127.0.0.1:7900", text)
        self.assertIn("https_proxy=http://127.0.0.1:7900", text)
        self.assertIn("HTTPS_PROXY=http://127.0.0.1:7900", text)
        self.assertTrue(proxy.is_enabled())
        publish.assert_called_once()

    def test_disable_removes_env_file(self):
        proxy = self._proxy()
        with mock.patch("catmeta.system._publish_session_env"):
            proxy.apply()
            ok, message = proxy.disable()
        self.assertTrue(ok, message)
        self.assertFalse(self.file.exists())
        self.assertFalse(proxy.is_enabled())
