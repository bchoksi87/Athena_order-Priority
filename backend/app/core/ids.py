"""Sortable unique identifiers (ULID-like, no external dependency)."""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_id(prefix: str = "") -> str:
    """Return a time-sortable 26-char id, optionally prefixed (``"sch_01H..."``)."""
    ts = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    ulid = _encode(ts, 10) + _encode(rand, 16)
    return f"{prefix}_{ulid}" if prefix else ulid
