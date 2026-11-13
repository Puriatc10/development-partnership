"""Ensure the repository root is importable when pytest is invoked bare.

`python -m pytest` already puts the CWD on ``sys.path``; this hook keeps
``pytest`` (console script) working the same way so ``intelligence.ipp`` can be
imported from ``tests/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
