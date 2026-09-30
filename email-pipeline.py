#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for candidate in (ROOT / "src", ROOT / "email_pipeline", ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.__main__ import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("scan-enqueue")
    raise SystemExit(main())
