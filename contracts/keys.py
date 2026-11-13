"""Canonical hashing helpers shared by every layer.

Idempotency keys and evidence content hashes must be computed identically
wherever they are produced (adapters, repositories, orchestration), so the
primitive lives in ``contracts`` rather than in one of its consumers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 safe."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def content_key(value: Any) -> str:
    """sha256 over the canonical rendering of ``value``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    """sha256 of a text payload (used for evidence excerpts)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
