import unittest

from catmeta import gsettings


class GSettingsTests(unittest.TestCase):
    def test_missing_schema_returns_none(self):
        # 缺失 schema 时 Gio.Settings.new() 会 g_error 中止进程，必须提前判断。
        self.assertFalse(gsettings.schema_installed("catmeta.does.not.exist"))
        self.assertIsNone(gsettings.new("catmeta.does.not.exist"))

    def test_existing_schema(self):
        if not gsettings.schema_installed("org.gnome.desktop.interface"):
            self.skipTest("系统未安装 gsettings-desktop-schemas")
        settings = gsettings.new("org.gnome.desktop.interface")
        self.assertIsNotNone(settings)
