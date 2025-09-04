# -*- coding: utf-8 -*-
import os
from dataclasses import dataclass
from typing import Optional

from .constants import (
    API_PATH,
    DEFAULT_DIVISOR,
    DEFAULT_LOOKBACK_MIN,
    DEFAULT_STATE_PATH,
)


@dataclass(frozen=True)
class EnvConfig:
    domain: str
    api_base: str
    token: str
    field_installment_id: int
    custom_main_budget_id: Optional[int]
    lookback_min: int
    formula_type: str
    formula_divisor: float
    formula_percent: float
    state_path: str


def _to_int(val: str, default: int) -> int:
    try:
        return int(val)
    except Exception:
        return default


def _to_float(val: str, default: float) -> float:
    try:
        return float(val)
    except Exception:
        return default


def _to_opt_int(val: str) -> Optional[int]:
    v = (val or "").strip()
    return int(v) if v else None


def load_env() -> EnvConfig:
    domain = os.environ.get("AMO_DOMAIN", "").strip()
    token = os.environ.get("AMO_TOKEN", "").strip()
    field_raw = os.environ.get("FIELD_INSTALLMENT_ID", "0")
    custom_raw = os.environ.get("CUSTOM_MAIN_BUDGET_ID", "").strip()
    lookback_raw = os.environ.get("WORKER_LOOKBACK_MIN", "")
    formula_type = os.environ.get("FORMULA_TYPE", "fixed_divisor").strip()
    div_raw = os.environ.get("FORMULA_DIVISOR", str(DEFAULT_DIVISOR))
    formula_divisor = _to_float(div_raw or str(DEFAULT_DIVISOR),
                                DEFAULT_DIVISOR)

    formula_percent = _to_float(
        os.environ.get("FORMULA_PERCENT", "0") or "0", 0.0
    )
    state_path = os.environ.get("STATE_PATH", DEFAULT_STATE_PATH)

    if not domain:
        raise SystemExit("AMO_DOMAIN is required")
    if not token:
        raise SystemExit("AMO_TOKEN is required")

    field_id = _to_int(field_raw, 0)
    if not field_id:
        raise SystemExit("FIELD_INSTALLMENT_ID is required")

    custom_id = _to_opt_int(custom_raw)
    lookback_min = _to_int(lookback_raw or str(DEFAULT_LOOKBACK_MIN),
                           DEFAULT_LOOKBACK_MIN)

    api_base = f"https://{domain}{API_PATH}"

    return EnvConfig(
        domain=domain,
        api_base=api_base,
        token=token,
        field_installment_id=field_id,
        custom_main_budget_id=custom_id,
        lookback_min=lookback_min,
        formula_type=formula_type,
        formula_divisor=formula_divisor,
        formula_percent=formula_percent,
        state_path=state_path,
    )
