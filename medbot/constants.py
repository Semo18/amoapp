# constants.py
# Общие константы и пресеты, используемые в нескольких модулях.

# 🔴 В этом файле собраны значения, которые повторялись в коде
# и параметры, которые удобно менять централизованно.

# --- Telegram/Web ---
ALLOWED_ORIGINS = [  # 🔴 CORS источники
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://amo.ap-development.com",
]

TELEGRAM_TYPING_REFRESH_SEC = 4  # 🔴 как часто обновлять ChatAction

# --- OpenAI Delivery Splitting ---
SPLIT_FIRST_LIMIT = 1500  # 🔴 первая часть ответа
SPLIT_SECOND_LIMIT = 2500  # 🔴 вторая часть ответа
TELEGRAM_TEXT_LIMIT = 4096  # 🔴 жёсткий лимит Telegram

# --- OpenAI Run statuses considered "active" ---
ACTIVE_RUN_STATUSES = {
    "queued",
    "in_progress",
    "requires_action",
    "cancelling",
}

# --- Redis keys ---
REDIS_THREAD_KEY = "medbot:tchat:thread"
REDIS_LAST_SEEN_KEY = "medbot:last_seen"
REDIS_ACK_ONCE_PREFIX = "medbot:ack:"
REDIS_LAST_ACK_PREFIX = "medbot:last_ack:"
REDIS_LEAD_ID_KEY = "medbot:tchat:lead_id"

# --- Defaults ---
DEFAULT_REPLY_DELAY_SEC = 60  # 🔴 задержка авто-ответа по умолчанию

# --- Таймауты и лимиты времени ---

# HTTP-запросы
HTTP_TIMEOUT_SEC = 10  # 🔴 общий таймаут HTTP-сессий
AMO_REQUEST_TIMEOUT_SEC = 15  # 🔴 таймаут для amoCRM API
TELEGRAM_FORWARD_TIMEOUT_SEC = 5  # 🔴 таймаут пересылки в amoCRM webhook

# OpenAI обработка
OPENAI_RUN_TIMEOUT_SEC = 600  # 🔴 максимальное время ожидания run (10 мин)
OPENAI_RUN_POLL_INTERVAL_SEC = 2  # 🔴 интервал проверки статуса run
OPENAI_THREAD_IDLE_TIMEOUT_SEC = 60  # 🔴 таймаут ожидания освобождения треда
OPENAI_THREAD_IDLE_POLL_SEC = 0.4  # 🔴 интервал проверки треда

# Тайпинг индикаторы
TELEGRAM_TYPING_DURATION_SEC = 60  # 🔴 длительность показа "печатает..." во время обработки
TELEGRAM_TYPING_ACK_DURATION_SEC = 20  # 🔴 длительность тайпинга для авто-квитка
TELEGRAM_TYPING_RESPONSE_DURATION_SEC = 15  # 🔴 длительность тайпинга при отправке ответа
TELEGRAM_TYPING_TAIL_DURATION_SEC = 10  # 🔴 длительность тайпинга для остальных частей

# ACK (авто-квитки)
ACK_COOLDOWN_SEC = 60  # 🔴 интервал между авто-квитками (1 минута)
ACK_ONCE_TTL_SEC = 24 * 3600  # 🔴 время жизни пометки "уже отправили" (24 часа)

# Периодические задачи
AMO_TOKEN_REFRESH_INTERVAL_SEC = 12 * 3600  # 🔴 интервал обновления токена (12 часов)
AMO_TOKEN_REFRESH_RETRY_SEC = 300  # 🔴 интервал повтора при ошибке (5 минут)
