import os
import unittest
from unittest import mock

from catmeta.system import SystemProxy


class SystemProxyAvailabilityTests(unittest.TestCase):
    def _check(self, desktop: str, schemas: str) -> bool:
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": desktop}, clear=False):
            with mock.patch("catmeta.system.shutil.which", return_value="/usr/bin/gsettings"):
                with mock.patch("catmeta.system._run_gsettings", return_value=(True, schemas)):
                    return SystemProxy().available

    def test_xfce_is_unavailable_even_with_schema(self):
        # XFCE 不读 org.gnome.system.proxy，开关必须按不可用处理。
        self.assertFalse(self._check("XFCE", "org.gnome.system.proxy\n"))

    def test_lxqt_is_unavailable(self):
        self.assertFalse(self._check("LXQt", "org.gnome.system.proxy\n"))

    def test_cinnamon_is_available(self):
        self.assertTrue(self._check("X-Cinnamon", "org.gnome.system.proxy\n"))

    def test_gnome_is_available(self):
        self.assertTrue(self._check("GNOME", "org.gnome.system.proxy\n"))

    def test_missing_schema_is_unavailable(self):
        self.assertFalse(self._check("X-Cinnamon", "org.gnome.desktop.interface\n"))
