"""通用工具函数。"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """递归合并字典；override 优先，且不会修改原对象。"""
    result: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def format_speed(value: float | int | None) -> str:
    """把字节/秒格式化为紧凑的速度文本。"""
    value = float(value or 0)
    if value < 1024:
        return f"{max(value, 0):.0f} B/s"
    units = ["KB/s", "MB/s", "GB/s"]
    for unit in units:
        value /= 1024.0
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if value < 100 else f"{value:.0f} {unit}"
    return "0 B/s"


def format_bytes(value: float | int | None) -> str:
    value = float(value or 0)
    if value <= 0:
        return "0 B"
    if value < 1024:
        return f"{value:.0f} B"
    units = ["KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        value /= 1024.0
        if value < 1024 or unit == units[-1]:
            if value >= 100:
                return f"{value:.0f} {unit}"
            if value >= 10:
                return f"{value:.1f} {unit}"
            return f"{value:.2f} {unit}"
    return "0 B"


def format_duration(seconds: float | int | None) -> str:
    seconds = max(0, int(seconds or 0))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def format_timestamp(value: float | int | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not value:
        return "从未"
    try:
        return datetime.fromtimestamp(float(value)).strftime(fmt)
    except (ValueError, OSError, OverflowError):
        return "未知"


def time_ago(value: float | int | None) -> str:
    if not value:
        return "从未更新"
    delta = max(0, time.time() - float(value))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 86400 * 30:
        return f"{int(delta // 86400)} 天前"
    return format_timestamp(value, "%Y-%m-%d")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def ellipsize(text: str, limit: int = 64) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_int(value: Any, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def latency_class(delay: Any) -> str:
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        return "latency-timeout"
    if delay <= 0:
        return "latency-timeout"
    if delay < 200:
        return "latency-good"
    if delay < 500:
        return "latency-medium"
    return "latency-bad"


def latency_text(delay: Any) -> str:
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        return "—"
    return f"{delay} ms" if delay > 0 else "超时"


def subscription_usage(upload: int, download: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return clamp((upload + download) / total, 0.0, 1.0)


def humanize_size_short(value: float | int | None) -> str:
    return format_bytes(value).replace(" ", "")


def safe_filename(name: str, fallback: str = "profile") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "-", name).strip("-.")
    return cleaned[:80] or fallback


def mask_secret(secret: str) -> str:
    if not secret:
        return "（无）"
    if len(secret) <= 6:
        return "•" * len(secret)
    return secret[:3] + "•" * 8 + secret[-2:]


def seconds_to_human_rules(count: int) -> str:
    return f"{count:,} 条规则"


def pct(value: float) -> str:
    return f"{int(round(value * 100))}%"


def is_truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, max(0, math.ceil(p / 100 * len(values)) - 1))
    return values[idx]
