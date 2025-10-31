# app.py — FastAPI-приложение бота: Telegram ↔ amoCRM ↔ OpenAI
# Каждая строка снабжена коротким комментарием; новые правки — # 🔴

import os  # доступ к переменным окружения
import logging  # базовое логирование
from typing import Optional, Union, Dict, Any  # типизация

from fastapi import FastAPI, Request, HTTPException, Query  # веб-ядро
from fastapi.middleware.cors import CORSMiddleware  # CORS-политика

from dotenv import load_dotenv  # загрузка .env

from aiogram import Bot, Dispatcher  # Telegram SDK
from aiogram.types import Update  # модель апдейта

from openai import OpenAI  # самотест OpenAI

# локальные модули (структура проекта сохранена)
from bot import setup_handlers  # регистрация хэндлеров
from admin_api import router as admin_router  # маршруты админки
from repo import fetch_messages, upload_file_to_amo  # БД и файлы в amo
from constants import (  # общие константы проекта
    ALLOWED_ORIGINS,
    TELEGRAM_FORWARD_TIMEOUT_SEC,
    AMO_TOKEN_REFRESH_INTERVAL_SEC,
    AMO_TOKEN_REFRESH_RETRY_SEC,
)

# 🔴 — функции работы с amoCRM оставляем в отдельном модуле
from amo_client import (  # 🔴
    refresh_access_token,        # 🔁 обновление токена amoCRM
    create_lead_in_amo,          # создание контакта+сделки
    add_file_note,               # прикрепление файла к сделке
    send_chat_message_v2,        # отправка в Chat API (amojo)
)

# ======================
#      Инициализация
# ======================

load_dotenv()  # загружаем .env до чтения переменных

logging.basicConfig(  # настройка логгера
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # токен Telegram
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")  # секрет вебхука
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # базовый URL

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # ключ OpenAI
ASSISTANT_ID = os.getenv("ASSISTANT_ID")  # ID ассистента OpenAI

if not BOT_TOKEN:  # валидация критичных переменных
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set")
if not ASSISTANT_ID:
    raise RuntimeError("ASSISTANT_ID is not set")

AMO_WEBHOOK_URL = os.getenv("AMO_WEBHOOK_URL", "")  # URL вебхука в amo
AMO_API_URL = os.getenv("AMO_API_URL", "")  # базовый API amo
AMO_ENABLED = bool(AMO_WEBHOOK_URL or AMO_API_URL)  # флаг интеграции

bot = Bot(BOT_TOKEN)  # инициализация Telegram-бота
dp = Dispatcher()  # роутер aiogram
app = FastAPI(title="medbot")  # приложение FastAPI

# ======================
#  Периодический refresh
# ======================

@app.on_event("startup")  # хук старта приложения
async def periodic_token_refresh() -> None:
    """Фоновое обновление токена amoCRM по расписанию."""
    import asyncio  # локальный импорт — не засоряем глобалку

    async def refresher() -> None:
        while True:  # бесконечный цикл до остановки сервиса
            try:
                logging.info("♻️ Scheduled amoCRM token refresh...")
                await refresh_access_token()  # 🔁 запрос токена
                logging.info(
                    "✅ amoCRM token refreshed successfully (scheduled)"
                )
                await asyncio.sleep(  # пауза до следующего окна
                    AMO_TOKEN_REFRESH_INTERVAL_SEC
                )
            except Exception as exc:  # сетевые/401 и т.п.
                logging.warning("⚠️ Refresh failed: %s", exc)
                logging.info("🔁 Retry in %s sec",
                             AMO_TOKEN_REFRESH_RETRY_SEC)
                await asyncio.sleep(AMO_TOKEN_REFRESH_RETRY_SEC)

    asyncio.create_task(refresher())  # фоновая задача
    # 🔴

# ======================
#         CORS
# ======================

app.add_middleware(  # разрешённые источники фронтенда
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
#  OpenAI self-test API
# ======================

@app.get("/medbot/openai-selftest")
async def openai_selftest() -> Dict[str, Any]:
    """Проверка доступности моделей и ассистента."""
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)  # клиент SDK
        mdl = client.models.retrieve("gpt-4o-mini")  # тест модели
        ast = client.beta.assistants.retrieve(ASSISTANT_ID)  # ассистент
        return {"ok": True, "model": mdl.id, "assistant_id": ast.id,
                "assistant_name": getattr(ast, "name", None)}
    except Exception as exc:
        logging.exception("OpenAI selftest failed")  # стек в логи
        raise HTTPException(status_code=500, detail=str(exc))

# ======================
#      Telegram webhook
# ======================

from storage import (  # импортируем после настройки app
    get_lead_id as redis_get_lead_id,  # маппинг chat_id → lead_id
    set_lead_id as redis_set_lead_id,
)

@app.post("/medbot/webhook")
async def telegram_webhook(request: Request) -> Dict[str, Any]:
    """Основная точка входа Telegram-апдейтов."""
    # защитный секрет, чтобы не принять чужой вызов
    secret = request.headers.get("x-telegram-bot-api-secret-token")
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret")

    data = await request.json()  # читаем update как dict
    update = Update.model_validate(data)  # валидация aiogram-моделью
    await dp.feed_update(bot, update)  # отдаём хэндлерам aiogram

    # зеркалим апдейт в amoCRM при наличии URL
    if AMO_WEBHOOK_URL:
        try:
            import aiohttp  # локальный импорт — экономим импорты
            async with aiohttp.ClientSession() as s:  # сессия HTTP
                await s.post(  # отправка «как есть», с таймаутом
                    AMO_WEBHOOK_URL,
                    json=data,
                    timeout=TELEGRAM_FORWARD_TIMEOUT_SEC,
                )
                logging.info("📨 Update forwarded to amoCRM webhook")
        except Exception as e:
            logging.warning("⚠️ Forward to amoCRM failed: %s", e)

        # ниже — логика сделки/заметок/чата amoCRM
    if AMO_API_URL and os.getenv("AMO_ACCESS_TOKEN"):  # включена ли amo
        try:
            msg = data.get("message") or {}  # блок сообщения
            chat_id_opt = (msg.get("chat") or {}).get("id")  # int|None
            text = (msg.get("text") or "").strip()  # текст апдейта
            username = ((msg.get("from") or {}).get("username") or "unknown")

            if chat_id_opt is None:  # защита от нестандартных апдейтов
                logging.info("ℹ️ no chat_id in update; skip amo flow")
                return {"ok": True}

            chat_id = int(chat_id_opt)

            # Флаг: iMbox сам создаёт сделки (через MedBot Bridge)
            imbox_autocreate = os.getenv("AMO_IMBOX_AUTOCREATE", "1") == "1"

            # Пробуем достать связку chat_id → lead_id из Redis
            lead_id: Optional[Union[str, int]] = redis_get_lead_id(chat_id)

            # Если включено автосоздание — пробуем найти существующую сделку
            if imbox_autocreate and not lead_id:
                lead_id = await get_latest_lead_for_chat(chat_id)
                if lead_id:
                    logging.info("♻️ Existing lead %s found for chat %s",
                                lead_id, chat_id)
                    redis_set_lead_id(chat_id, str(lead_id))

                    # Ждём, пока сделка “дозреет” в amo (5 сек)
                    await asyncio.sleep(5)

                    # Проверяем, что сделка именно Telegram, не сторонняя
                    lead_name = await get_lead_name(lead_id)
                    if lead_name and (
                        "telegram" in lead_name.lower() or str(chat_id) in lead_name
                    ):
                        target_pipeline_id = int(
                            os.getenv("AMO_PIPELINE_AI_ID", "10176698")
                        )
                        await move_lead_to_pipeline(lead_id, target_pipeline_id)
                    else:
                        logging.info("🛑 Lead %s not Telegram — skip move", lead_id)

            # Если автосоздание выключено — создаём лид вручную
            elif not imbox_autocreate and not lead_id:
                lead_id = await create_lead_in_amo(
                    chat_id=chat_id,
                    username=username,
                )
                if lead_id:
                    redis_set_lead_id(chat_id, str(lead_id))
                    logging.info("✅ lead %s created for chat %s",
                                lead_id, chat_id)
                else:
                    logging.warning("⚠️ lead not created for chat %s", chat_id)

            # Отправляем клиентский текст в amojo Chat API (iMbox)
            if text:
                scope_id = os.getenv("AMO_CHAT_SCOPE_ID", "").strip()
                if not scope_id:
                    logging.warning("⚠️ AMO_CHAT_SCOPE_ID is empty")
                else:
                    ok = await send_chat_message_v2(
                        scope_id=scope_id,
                        chat_id=chat_id,
                        text=text,
                        username=username,
                    )
                    if not ok:
                        logging.warning("⚠️ ChatAPI send returned false")

            # Вложения отправляем только если знаем lead_id
            if lead_id and ("document" in msg or "photo" in msg):
                file_id: Optional[str] = None
                file_name = ""

                if "document" in msg:
                    file_id = msg["document"]["file_id"]
                    file_name = msg["document"].get("file_name", "file.bin")
                elif "photo" in msg:
                    file_id = msg["photo"][-1]["file_id"]
                    file_name = "photo.jpg"

                if file_id:
                    try:
                        file_info = await bot.get_file(file_id)
                        file_bytes = await bot.download_file(file_info.file_path)
                        uuid = await upload_file_to_amo(file_name, file_bytes.read())
                        if uuid:
                            ok = await add_file_note(
                                lead_id=str(lead_id),
                                uuid=uuid,
                                file_name=file_name or "file.bin",
                            )
                            if not ok:
                                logging.warning("⚠️ add_file_note failed")
                        else:
                            logging.warning("⚠️ upload_file_to_amo empty")
                    except Exception as ex:
                        logging.warning("⚠️ file attach flow failed: %s", ex)

        except Exception as e:
            logging.warning("⚠️ amoCRM linkage failed: %s", e)

    return {"ok": True}


# ======================
#        Healthcheck
# ======================

@app.get("/medbot/health")
async def health() -> Dict[str, str]:
    """Проверка живости сервиса."""
    return {"status": "ok"}

# ======================
#   Входящий Chat API
# ======================

import hashlib  # подпись входящих событий amojo
import hmac  # HMAC-SHA1

def _hmac_sha1_hex(data: str, secret: str) -> str:
    """Подписание строки как hex(lower)."""
    mac = hmac.new(secret.encode("utf-8"),
                   data.encode("utf-8"),
                   digestmod="sha1")
    return mac.hexdigest().lower()

@app.post("/medbot/amo-webhook/{scope_id}")
async def amo_chat_webhook(scope_id: str, request: Request):
    """Приём событий Chat API (сообщения менеджера из карточки)."""
    secret = os.getenv("AMO_CHAT_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="chat secret empty")

    date_hdr = request.headers.get("Date", "")
    ct_hdr = request.headers.get("Content-Type", "application/json")
    md5_hdr = (request.headers.get("Content-MD5", "") or "").lower()
    sig_hdr = (request.headers.get("X-Signature", "") or "").lower()

    body = await request.body()  # байты тела для MD5

    real_md5 = hashlib.md5(body).hexdigest().lower()  # контрольная сумма
    if md5_hdr and md5_hdr != real_md5:  # валидация MD5 если пришёл
        raise HTTPException(status_code=400, detail="Bad Content-MD5")

    path = f"/medbot/amo-webhook/{scope_id}"  # путь для подписи

    sign_str = "\n".join([  # строка подписи по схеме amojo
        request.method.upper(),
        md5_hdr,
        ct_hdr,
        date_hdr,
        path,
    ])
    expected = _hmac_sha1_hex(sign_str, secret)  # расчёт подписи

    if sig_hdr and sig_hdr != expected:  # несоответствие подписи
        raise HTTPException(status_code=401, detail="Bad signature")

    try:
        payload = await request.json()  # парсим JSON
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    evt = payload.get("event_type")  # тип события
    if evt != "new_message":  # игнорируем прочие события
        return {"status": "ignored"}

    # v2-формат: верхний уровень + payload.message
    conv_id = (payload.get("conversation_id") or "").strip()  # 🔴
    if not conv_id.startswith("tg_"):  # не наш разговор
        return {"status": "ignored"}

    try:
        chat_id = int(conv_id.replace("tg_", "", 1))  # извлекаем ID
    except ValueError:
        return {"status": "ignored"}  # странный conv_id

    msg = (payload.get("payload") or {}).get("message") or {}  # текст
    text = (msg.get("text") or "").strip()
    if not text:
        return {"status": "ok"}  # пустые не шлём

    try:
        await bot.send_message(chat_id, f"💬 Менеджер: {text}")  # ответ
    except Exception:  # не роняем вебхук
        pass

    return {"status": "ok"}

# ======================
#     Админ-хелперы
# ======================

app.include_router(admin_router)  # подключаем админ-роуты
setup_handlers(dp)  # регистрируем Telegram-хэндлеры

@app.get("/admin/set_webhook")
async def set_webhook() -> Dict[str, Any]:
    """Установка Telegram-webhook после деплоя."""
    if not BASE_URL:
        raise HTTPException(500, "BASE_URL not set")

    await bot.set_webhook(  # настраиваем вебхук бота
        url=f"{BASE_URL}/medbot/webhook",
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )
    return {"ok": True, "url": f"{BASE_URL}/medbot/webhook"}

# ======================
#    API для админ-панели
# ======================

@app.get("/admin-api/messages")
async def api_messages(
    chat_id: int = Query(..., description="ID чата (обязателен)"),
    limit: int = Query(20, ge=1, le=200),
    before_id: Optional[int] = Query(None),
    after_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None, min_length=1),
    direction: Optional[int] = Query(None, ge=0, le=1),
    content_type: Optional[str] = Query(None),
    order: str = Query("desc", regex="^(asc|desc)$"),
) -> Dict[str, Any]:
    """Выдача сообщений с фильтрами и пагинацией."""
    try:
        data = fetch_messages(
            chat_id=chat_id,
            limit=limit,
            before_id=before_id,
            after_id=after_id,
            q=q,
            direction=direction,
            content_type=content_type,
            order=order,
        )
        return data
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
