# -*- coding: utf-8 -*-

# Логи
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
DEFAULT_LOG_LEVEL = "INFO"

# API / сеть
API_PATH = "/api/v4"
LEADS_LIMIT = 250
SESSION_TIMEOUT = 30  # seconds
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

# Формулы
DEFAULT_DIVISOR = 12.0
DEFAULT_LOOKBACK_MIN = 10
DEFAULT_OVERLAP_SEC = 60  # «подклейка» при чтении last_since

# Состояние воркера (файл с last_success_updated_at)
DEFAULT_STATE_PATH = "/var/www/app/.state/last_since.json"
