# -*- coding: utf-8 -*-
import datetime as dt
import os
from typing import Any, Dict

from flask import Flask, jsonify, request

from .logging_conf import setup_logging
from .worker import init_env_api, process_once

setup_logging(None)
ENV, API = init_env_api()
RUN_TOKEN = os.environ.get("RUN_TOKEN", "").strip()

app = Flask(__name__)


@app.get("/health")
def health() -> Any:
    cfg: Dict[str, Any] = {
        "domain": ENV.domain,
        "pipeline_id": ENV.pipeline_id,
        "pipeline_name": ENV.pipeline_name,
        # ниже — не секреты, полезно для отладки
        "field_installment_id": ENV.field_installment_id,
        "source_budget_field_id": ENV.custom_main_budget_id,
        "lookback_min": ENV.lookback_min,
        "formula": {
            "type": ENV.formula_type,
            "divisor": ENV.formula_divisor,
            "percent": ENV.formula_percent,
        },
    }
    return jsonify(ok=True, ts=dt.datetime.utcnow().isoformat(), **cfg)


def _token_ok() -> bool:
    # Если RUN_TOKEN не задан, доступ открыт (как раньше).
    if not RUN_TOKEN:
        return True
    provided = request.headers.get("X-Run-Token", "").strip()
    return provided == RUN_TOKEN


@app.route("/run-once", methods=["POST", "GET"])
def run_once() -> Any:
    if not _token_ok():
        return jsonify(ok=False, error="forbidden"), 403
    stats = process_once(ENV, API)
    return jsonify(ok=True, **stats)
