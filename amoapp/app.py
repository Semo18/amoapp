# -*- coding: utf-8 -*-
import datetime as dt
import os
from typing import Any

from flask import Flask, jsonify, request

from .logging_conf import setup_logging
from .worker import init_env_api, process_once

setup_logging(None)
ENV, API = init_env_api()
RUN_TOKEN = os.environ.get("RUN_TOKEN", "").strip()

app = Flask(__name__)


@app.get("/health")
def health() -> Any:
    return jsonify(
        ok=True,
        ts=dt.datetime.utcnow().isoformat(),
        domain=ENV.domain,
    )


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
