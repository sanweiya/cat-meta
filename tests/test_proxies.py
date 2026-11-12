import unittest

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from catmeta.ui.pages.proxies import ACTION_COLUMN_WIDTH, ProxiesPage


class ActionHitAreaTests(unittest.TestCase):
    def test_connect_and_test_hit_areas(self):
        width = ACTION_COLUMN_WIDTH
        self.assertEqual(ProxiesPage._action_at(None, 8, width), "connect")
        self.assertEqual(ProxiesPage._action_at(None, 52, width), "connect")
        self.assertEqual(ProxiesPage._action_at(None, 70, width), "test")
        self.assertEqual(ProxiesPage._action_at(None, 108, width), "test")
        self.assertEqual(ProxiesPage._action_at(None, 2, width), "")
        self.assertEqual(ProxiesPage._action_at(None, 116, width), "")

    def test_untested_delay_is_blank_and_timeout_after_test(self):
        store = Gtk.ListStore(str, str, object, str)
        store.append(["节点", "SS", None, ""])
        cell = Gtk.CellRendererText()
        tree_iter = store.get_iter_first()
        ProxiesPage._latency_cell(None, None, cell, store, tree_iter, None)
        self.assertEqual(cell.get_property("text"), "")
        self.assertFalse(cell.get_property("foreground-set"))

        store.set_value(tree_iter, 2, 0)
        ProxiesPage._latency_cell(None, None, cell, store, tree_iter, None)
        self.assertEqual(cell.get_property("text"), "超时")
        self.assertTrue(cell.get_property("foreground-set"))

        store.set_value(tree_iter, 2, 123)
        ProxiesPage._latency_cell(None, None, cell, store, tree_iter, None)
        self.assertEqual(cell.get_property("text"), "123 ms")


if __name__ == "__main__":
    unittest.main()
