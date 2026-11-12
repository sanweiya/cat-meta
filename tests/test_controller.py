import unittest
from types import SimpleNamespace
from unittest import mock

from catmeta.controller import Controller


class ShutdownTests(unittest.TestCase):
    def make_controller(self, *, stop_core: bool, proxy_enabled: bool = True) -> Controller:
        controller = Controller.__new__(Controller)
        controller._shutting_down = False
        controller.stop_ticker = mock.Mock()
        controller._stop_websockets = mock.Mock()
        controller.settings = SimpleNamespace(stop_core_on_quit=stop_core, system_proxy=True)
        controller.system_proxy = mock.Mock()
        controller.system_proxy.is_enabled.return_value = proxy_enabled
        controller.system_proxy.disable.return_value = (True, "系统代理已关闭")
        controller.core = mock.Mock()
        controller.state = "running"
        return controller

    def test_manual_quit_always_disables_system_proxy(self):
        controller = self.make_controller(stop_core=False)
        controller.shutdown()

        controller.system_proxy.disable.assert_called_once_with()
        controller.core.stop.assert_not_called()
        # 保留偏好，供下次启动恢复。
        self.assertTrue(controller.settings.system_proxy)
        self._assert_proxy_not_running(controller)

    def test_quit_stops_core_but_keeps_proxy_preference(self):
        controller = self.make_controller(stop_core=True)
        controller.shutdown()

        controller.system_proxy.disable.assert_called_once_with()
        controller.core.stop.assert_called_once_with()
        self.assertTrue(controller.settings.system_proxy)

    def test_quit_skips_disable_when_proxy_is_not_ours(self):
        controller = self.make_controller(stop_core=True, proxy_enabled=False)
        controller.shutdown()

        controller.system_proxy.disable.assert_not_called()
        controller.core.stop.assert_called_once_with()

    @staticmethod
    def _assert_proxy_not_running(controller: Controller) -> None:
        controller._stop_websockets.assert_called_once_with()
        controller.stop_ticker.assert_called_once_with()
        assert controller._shutting_down is True


class NodePollVisibilityTests(unittest.TestCase):
    def make_controller(self, visible: bool) -> Controller:
        controller = Controller.__new__(Controller)
        controller._ui_visible = visible
        controller._shutting_down = False
        controller.state = "running"
        controller.core = mock.Mock(running=True)
        controller._last_node_poll = 0.0
        controller._poll_current_proxy = mock.Mock()
        return controller

    def test_hidden_window_does_not_poll_current_node(self):
        controller = self.make_controller(False)
        with mock.patch("catmeta.controller.threading.Thread") as thread:
            controller.refresh_current_node(force=True)
        thread.assert_not_called()

    def test_visible_window_polls_current_node(self):
        controller = self.make_controller(True)
        with mock.patch("catmeta.controller.threading.Thread") as thread:
            controller.refresh_current_node(force=True)
        thread.assert_called_once()


class CurrentProxyTests(unittest.TestCase):
    def test_prefers_named_selector_group(self):
        data = {
            "GLOBAL": {"type": "Selector", "now": "DIRECT", "all": []},
            "自动选择": {"type": "URLTest", "now": "日本", "all": ["日本"]},
            "选择节点": {"type": "Selector", "now": "香港", "all": ["香港", "日本"]},
        }
        self.assertEqual(Controller._pick_current_proxy(data), ("选择节点", "香港"))

    def test_falls_back_to_global(self):
        data = {"GLOBAL": {"type": "Selector", "now": "DIRECT", "all": []}}
        self.assertEqual(Controller._pick_current_proxy(data), ("GLOBAL", "DIRECT"))

    def test_ignores_groups_without_current_node(self):
        data = {"选择节点": {"type": "Selector", "now": "", "all": ["香港"]}}
        self.assertEqual(Controller._pick_current_proxy(data), ("", ""))


if __name__ == "__main__":
    unittest.main()
