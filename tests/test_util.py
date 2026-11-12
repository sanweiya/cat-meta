import unittest

from catmeta.util import (
    deep_merge,
    format_bytes,
    format_duration,
    format_speed,
    latency_class,
    latency_text,
    safe_filename,
)


class UtilTests(unittest.TestCase):
    def test_deep_merge_does_not_mutate(self):
        base = {"a": {"x": 1}, "b": 2}
        override = {"a": {"y": 3}, "b": 4}
        result = deep_merge(base, override)
        self.assertEqual(result, {"a": {"x": 1, "y": 3}, "b": 4})
        self.assertEqual(base, {"a": {"x": 1}, "b": 2})

    def test_speed(self):
        self.assertEqual(format_speed(0), "0 B/s")
        self.assertEqual(format_speed(1024), "1.0 KB/s")
        self.assertIn("MB/s", format_speed(5 * 1024 * 1024))

    def test_bytes(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertIn("KB", format_bytes(2048))
        self.assertIn("GB", format_bytes(3 * 1024 ** 3))

    def test_duration(self):
        self.assertEqual(format_duration(5), "5 秒")
        self.assertEqual(format_duration(65), "1 分 5 秒")
        self.assertIn("小时", format_duration(3661))

    def test_latency(self):
        self.assertEqual(latency_class(50), "latency-good")
        self.assertEqual(latency_class(250), "latency-medium")
        self.assertEqual(latency_class(700), "latency-bad")
        self.assertEqual(latency_class(None), "latency-timeout")
        self.assertEqual(latency_text(None), "—")
        self.assertEqual(latency_text(123), "123 ms")

    def test_safe_filename(self):
        self.assertEqual(safe_filename("a/b c"), "a-b-c")
        self.assertEqual(safe_filename(""), "profile")


if __name__ == "__main__":
    unittest.main()
