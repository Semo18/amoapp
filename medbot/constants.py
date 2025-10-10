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
