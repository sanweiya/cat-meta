"""应用日志。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import APP_LOG, ensure_dirs


def setup_logging(debug: bool = False) -> logging.Logger:
    ensure_dirs()
    logger = logging.getLogger("catmeta")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        handler = RotatingFileHandler(APP_LOG, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except OSError:
        pass
    if debug:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)
    return logger
