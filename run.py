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


def main() -> int:
    app = CatMetaApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
