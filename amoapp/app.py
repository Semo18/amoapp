# -*- coding: utf-8 -*-
import datetime as dt
from typing import Any

from flask import Flask, jsonify

from .logging_conf import setup_logging
from .worker import init_env_api, process_once
# Тонкий прокси, чтобы gunicorn видел app:app

setup_logging(None)
ENV, API = init_env_api()
app = Flask(__name__)


@app.get("/health")
def health() -> Any:
    return jsonify(
        ok=True,
        ts=dt.datetime.utcnow().isoformat(),
        domain=ENV.domain,
    )


@app.post("/run-once")
def run_once() -> Any:
    stats = process_once(ENV, API)
    return jsonify(ok=True, **stats)
