# -*- coding: utf-8 -*-
import time
from typing import Optional

from .constants import DEFAULT_OVERLAP_SEC
from .utils import atomic_write_json, read_json


def read_last_success(path: str) -> Optional[int]:
    data = read_json(path)
    try:
        v = int(data.get("last_success_updated_at")) if data else None
        return v if v and v > 0 else None
    except Exception:
        return None


def write_last_success(path: str, ts: int) -> None:
    atomic_write_json(path, {"last_success_updated_at": int(ts)})


def compute_since(
    now_unix: int,
    lookback_min: int,
    last_success: Optional[int],
    overlap_sec: int = DEFAULT_OVERLAP_SEC,
) -> int:
    fallback = now_unix - lookback_min * 60
    if not last_success:
        return max(0, fallback)

    candidate = max(0, last_success - overlap_sec)
    # Берём «старее» из двух, чтобы ничего не потерять
    since = min(candidate, fallback)
    return max(0, since)
