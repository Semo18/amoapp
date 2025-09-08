# -*- coding: utf-8 -*-
import json
import time
from dataclasses import replace
from typing import Any, Dict, Optional, Tuple

from .amo_api import (
    AmoApi,
    calc_installment,
    first_cf_value,
    lead_budget,
    to_int_safe,
)
from .env import EnvConfig, load_env
from .logging_conf import get_logger, setup_logging
from .state import compute_since, read_last_success, write_last_success

log = get_logger("amo-calc")


def init_env_api() -> Tuple[EnvConfig, AmoApi]:
    """Инициализация окружения и API.
    Если pipeline_id не задан, но задано имя, резолвим его один раз.
    """
    env = load_env()
    setup_logging(None)
    api = AmoApi(api_base=env.api_base, token=env.token)

    if env.pipeline_id is None and env.pipeline_name:
        pid = api.resolve_pipeline_id(env.pipeline_name)
        if not pid:
            raise SystemExit(
                f"Pipeline '{env.pipeline_name}' not found"
            )
        env = replace(env, pipeline_id=pid)

    if env.pipeline_id:
        log.info("active pipeline filter: id=%s", env.pipeline_id)

    return env, api


def process_once(
    env: EnvConfig,
    api: AmoApi,
    minutes_back: Optional[int] = None,
) -> Dict[str, Any]:
    now = int(time.time())
    lookback_min = minutes_back if minutes_back is not None else \
        env.lookback_min

    last_success = read_last_success(env.state_path)
    since = compute_since(now, lookback_min, last_success)

    scanned = 0
    changed = 0
    same = 0
    max_seen = 0
    errors = 0

    for lead in api.fetch_updated_leads(
        since, pipeline_id=env.pipeline_id
    ):
        scanned += 1
        lead_id = int(lead.get("id"))
        max_seen = max(max_seen, int(lead.get("updated_at", now)))

        budget = lead_budget(lead, env.custom_main_budget_id)
        target = calc_installment(
            budget,
            env.formula_type,
            env.formula_divisor,
            env.formula_percent,
        )

        current_raw = first_cf_value(lead, env.field_installment_id)
        current = to_int_safe(current_raw)

        if target != current:
            try:
                api.patch_lead_fields(
                    lead_id,
                    {env.field_installment_id: target},
                )
                changed += 1
                log.info(
                    "lead %s: budget=%s -> %s (was %s)",
                    lead_id, budget, target, current,
                )
            except Exception as exc:
                errors += 1
                log.error("lead %s: patch failed: %s", lead_id, exc)
        else:
            same += 1

    # сохраняем прогресс даже при частичных ошибках
    new_last = max_seen if max_seen else now
    write_last_success(env.state_path, new_last)

    stats = {
        "scanned": scanned,
        "changed": changed,
        "skipped_same": same,
        "errors": errors,
        "since_unix": since,
        "saved_last_success": new_last,
        "pipeline_id": env.pipeline_id,
    }
    log.info("run stats: %s", json.dumps(stats, ensure_ascii=False))
    return stats
