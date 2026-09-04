#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ht_lead_radar.cli_v2 import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
