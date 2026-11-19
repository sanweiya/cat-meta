#!/usr/bin/env python3
"""Cat Meta 启动器。"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catmeta.logging_setup import setup_logging  # noqa: E402

setup_logging(debug=bool(os.environ.get("CATMETA_DEBUG")))
from catmeta.app import CatMetaApplication  # noqa: E402


# Linux 的 /proc/<pid>/comm 长度上限为 15 字节。
_PR_SET_NAME = 15


def _set_process_name(name: str = "cat-meta") -> None:
    """让系统监视器 / ps 的进程名列显示 cat-meta，而不是 python3。

    只修改 comm，不改变可执行文件路径和命令行；部分工具查看
    /proc/<pid>/exe 或 args 时仍会看到 python3，这是正常现象。
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_NAME, ctypes.c_char_p(name.encode("utf-8")[:15]), 0, 0, 0)
    except Exception:
        pass


def main() -> int:
    _set_process_name()
    app = CatMetaApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
