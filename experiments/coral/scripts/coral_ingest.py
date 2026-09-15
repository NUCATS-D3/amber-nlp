#!/usr/bin/env python3
"""Compatibility launcher for the experiment-local CORAL audit CLI."""

from __future__ import annotations

import sys
from pathlib import Path

# Direct file execution puts scripts/, not the repository root, on the import path.
# Keep this checkout-only bootstrap at the command boundary, never in parser modules.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from experiments.coral.audit import main  # noqa: E402

if __name__ == "__main__":
    main()
