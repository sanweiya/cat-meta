"""Cat Meta — 一个为 Linux Mint 打造的原生 mihomo 图形客户端。"""

import os
import sys

APP_NAME = "Cat Meta"
APP_ID = "com.github.catmeta.CatMeta"
VERSION = "1.0.0"

# 附带 PyYAML，使应用在未安装 python3-yaml 的机器上也能直接运行。
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

__all__ = ["APP_NAME", "APP_ID", "VERSION"]
