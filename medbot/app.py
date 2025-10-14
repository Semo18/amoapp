# app.py
# Основной файл FastAPI-приложения (medbot)
# Обрабатывает Telegram webhooks, интеграцию с amoCRM и OpenAI.

import os  # работа с переменными окружения и .env
import logging  # логирование системных событий
from typing import Optional, Dict, Any  # аннотации типов

# каркас веб-приложения и утилиты
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware  # CORS-доступ
from amo_client import refresh_access_token  # 🔁 автообновление токена
from storage import get_lead_id as redis_get_lead_id, set_lead_id as redis_set_lead_id  # 🔴
from amo_client import create_lead_in_amo  # 🔴
# (ниже ещё импортируем add_text_note / add_file_note после того, как добавим их в amo_client)

# Telegram SDK (aiogram)
from aiogram import Bot, Dispatcher
from aiogram.types import Update

# загрузка переменных окружения
from dotenv import load_dotenv

# используется для HTTP-запросов в amoCRM
import aiohttp

# клиент OpenAI для самопроверки соединения
from openai import OpenAI

# локальные модули проекта
from bot import setup_handlers  # регистрация Telegram-хэндлеров
from admin_api import router as admin_router  # REST для админки
from repo import fetch_messages  # получение сообщений из БД
from repo import upload_file_to_amo  # 🔴 загрузка файлов в amoCRM
from constants import (  # 🔴 общие константы
    ALLOWED_ORIGINS,
    TELEGRAM_FORWARD_TIMEOUT_SEC,
    AMO_TOKEN_REFRESH_INTERVAL_SEC,
    AMO_TOKEN_REFRESH_RETRY_SEC,
)
# ======================
#     НАСТРОЙКА БАЗЫ
# ======================

load_dotenv()  # подгружаем .env

logging.basicConfig(  # глобальное логирование
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# --- системные переменные ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # токен Telegram-бота
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")  # секрет вебхука
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # базовый URL

# --- параметры OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

# жёсткая валидация обязательных переменных
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set")
if not ASSISTANT_ID:
    raise RuntimeError("ASSISTANT_ID is not set")

# --- настройки amoCRM ---
AMO_WEBHOOK_URL = os.getenv("AMO_WEBHOOK_URL", "")
AMO_API_URL = os.getenv("AMO_API_URL", "")
AMO_ACCESS_TOKEN = os.getenv("AMO_ACCESS_TOKEN", "")
AMO_ENABLED = bool(AMO_WEBHOOK_URL or AMO_API_URL)

# --- создаём объекты Telegram SDK ---
bot = Bot(BOT_TOKEN)  # основной Telegram-бот
dp = Dispatcher()  # маршрутизатор aiogram
app = FastAPI(title="medbot")  # приложение FastAPI


# 🔴 Улучшенный автообновлятор amoCRM токена с повтором при ошибке
@app.on_event("startup")
async def periodic_token_refresh() -> None:
    """Автоматически обновляет amoCRM токен каждые 12 часов,
    с повтором через 5 минут при неудаче (например, сбой сети или 401).
    """
    import asyncio
    import logging

    async def refresher():
        while True:
            try:
                logging.info("♻️ Scheduled amoCRM token refresh...")
                await refresh_access_token()  # 🔴 обновление токена
                logging.info("✅ amoCRM token refreshed successfully (scheduled)")
                await asyncio.sleep(AMO_TOKEN_REFRESH_INTERVAL_SEC)  # 🔴 спим до следующего цикла
            except Exception as exc:
                logging.warning(f"⚠️ Failed scheduled token refresh: {exc}")
                logging.info("🔁 Retrying amoCRM token refresh in 5 minutes...")
                await asyncio.sleep(AMO_TOKEN_REFRESH_RETRY_SEC)  # 🔴 повтор через заданный интервал

    asyncio.create_task(refresher())  # 🔴 запускаем цикл в фоне



# ======================
#     НАСТРОЙКА CORS
# ======================

# разрешённые источники (фронтенд и тестовые домены)
# CORS источники берём из общего модуля констант # 🔴

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # список разрешённых доменов
    allow_credentials=True,
    allow_methods=["*"],  # разрешаем все HTTP-методы
    allow_headers=["*"],  # все заголовки
)

# ====================================================
#     ПРОСТАЯ ПРОВЕРКА СВЯЗНОСТИ С OpenAI API
# ====================================================

@app.get("/medbot/openai-selftest")
async def openai_selftest() -> Dict[str, Any]:
    """
    Проверка связности с OpenAI:
    1. Проверяем доступ к модели.
    2. Проверяем доступ к конкретному ассистенту.
    """
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        mdl = client.models.retrieve("gpt-4o-mini")  # тест модели
        ast = client.beta.assistants.retrieve(ASSISTANT_ID)  # тест ассистента
        return {
            "ok": True,
            "model": mdl.id,
            "assistant_id": ast.id,
            "assistant_name": getattr(ast, "name", None),
        }
    except Exception as exc:
        logging.exception("OpenAI selftest failed")
        raise HTTPException(status_code=500, detail=str(exc))

# ==========================================================
#       ГЛАВНЫЙ TELEGRAM WEBHOOK
# ==========================================================

@app.post("/medbot/webhook")
async def telegram_webhook(request: Request) -> Dict[str, Any]:
    """
    Основная точка приёма Telegram-сообщений.
    Стратегия:
    1. Обрабатываем сообщение aiogram.
    2. Дублируем апдейт в amoCRM.
    3. Создаём сделку и прикладываем файлы.
    """
    # Проверяем секретный токен вебхука (чтобы не принимать чужие запросы) # 🔴
    secret = request.headers.get(
        "x-telegram-bot-api-secret-token"
    )  # 🔴
    if secret != WEBHOOK_SECRET:  # 🔴
        raise HTTPException(status_code=403, detail="bad secret")

    # читаем JSON тела запроса (Telegram update)
    data = await request.json()

    # парсим в объект Update для aiogram
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)  # передаём на обработку aiogram

    # (1) Дублирование апдейта в amoCRM webhook
    if AMO_WEBHOOK_URL:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    AMO_WEBHOOK_URL, 
                    json=data, 
                    timeout=TELEGRAM_FORWARD_TIMEOUT_SEC
                )  # 🔴 используем константу таймаута
                logging.info("📨 Telegram update forwarded to amoCRM webhook")
        except Exception as e:
            logging.warning(f"⚠️ Failed to forward Telegram update: {e}")

    # app.py — замена блока создания сделки

    # ...
    # (2) Создание сделки / добавление заметки
    if AMO_API_URL and AMO_ACCESS_TOKEN:
        try:
            msg = data.get("message") or {}
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")
            username = msg.get("from", {}).get("username", "unknown")

            if not chat_id:  # страховка от нестандартных апдейтов
                return {"ok": True}

            # 1) Пытаемся использовать уже созданную сделку
            lead_id = redis_get_lead_id(chat_id)

            # 2) Если нет — создаём новую сделку через корректный клиент
            if not lead_id:
                lead_id = await create_lead_in_amo(chat_id=chat_id, username=username)
                if lead_id:
                    redis_set_lead_id(chat_id, str(lead_id))

            if not lead_id:
                logging.warning("⚠️ Lead is not created — skip notes")
                return {"ok": True}

            # 3) Текст сообщения — как примечание
            # 3) Сообщение клиента — отправляем как chat message в amoCRM
            if text:
                from amo_client import send_chat_message_to_amo
                await send_chat_message_to_amo(chat_id, text, username)

            # 4) Вложения: загрузим файл в amo + прикрепим к сделке
            if "document" in msg or "photo" in msg:
                file_name = None
                file_id = None
                if "document" in msg:
                    file_id = msg["document"]["file_id"]
                    file_name = msg["document"].get("file_name", "file.bin")
                elif "photo" in msg:
                    file_id = msg["photo"][-1]["file_id"]
                    file_name = "photo.jpg"

                if file_id:
                    file_info = await bot.get_file(file_id)
                    file_bytes = await bot.download_file(file_info.file_path)
                    uuid = await upload_file_to_amo(file_name, file_bytes.read())  # 🔴
                    if uuid:
                        from amo_client import add_file_note  # 🔴 импорт после добавления функции
                        await add_file_note(lead_id=str(lead_id), uuid=uuid, file_name=file_name)

        except Exception as e:
            logging.warning(f"⚠️ Failed to process amoCRM linkage: {e}")

    return {"ok": True}  # Telegram ждёт подтверждение

# ======================
#     HEALTHCHECK API
# ======================

@app.get("/medbot/health")
async def health() -> Dict[str, str]:
    """Проверка доступности сервера."""
    return {"status": "ok"}

# =====================================================
#   ВХОДЯЩИЕ СОБЫТИЯ ОТ AMOCRM
# =====================================================


@app.post("/medbot/amo-webhook")
async def amo_webhook(request: Request):
    """
    Приём событий amoCRM (новые сообщения из сделки).
    Если сообщение is_incoming=False (от менеджера) —
    пересылаем его в Telegram пользователю, но ассистент не отвечает.
    """
    data = await request.json()
    logging.info(f"📩 Вебхук amoCRM: {data}")

    try:
        events = data.get("_embedded", {}).get("events", [])
        for ev in events:
            if ev.get("type") != "chats_message":  # интересуют только чат-сообщения
                continue
            msg = ev.get("payload", {}).get("message", {})
            chat_id_str = ev.get("payload", {}).get("chat_id", "")
            if not chat_id_str.startswith("telegram-"):
                continue
            chat_id = int(chat_id_str.replace("telegram-", ""))
            text = msg.get("text", "")
            is_incoming = msg.get("is_incoming", True)

            # от менеджера → пользователю
            if not is_incoming and text:
                await bot.send_message(chat_id, text)
                logging.info(f"➡️ Отправлено пользователю из amoCRM: {text}")

    except Exception as e:
        logging.warning(f"⚠️ Ошибка обработки amoCRM webhook: {e}")

    return {"ok": True}


# =====================================================
#   ADMIN API, TELEGRAM ХЭНДЛЕРЫ, WEBHOOK SETUP
# =====================================================

setup_handlers(dp)  # регистрация Telegram-хэндлеров
app.include_router(admin_router)  # подключение REST админки

@app.get("/admin/set_webhook")
async def set_webhook() -> Dict[str, Any]:
    """Устанавливает Telegram webhook (вызвать после деплоя)."""
    if not BASE_URL:
        raise HTTPException(500, "BASE_URL not set")

    await bot.set_webhook(
        url=f"{BASE_URL}/medbot/webhook",
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )
    return {"ok": True, "url": f"{BASE_URL}/medbot/webhook"}

# =====================================================
#   API для сообщений админ-панели (поиск и фильтры)
# =====================================================

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
    """API админ-панели: выдача сообщений с фильтрами и пагинацией."""
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
