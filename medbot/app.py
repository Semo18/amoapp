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

# ======================
#     НАСТРОЙКА CORS
# ======================

# разрешённые источники (фронтенд и тестовые домены)
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://amo.ap-development.com",
]

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
    # Проверяем секретный токен вебхука
    if request.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
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
                await session.post(AMO_WEBHOOK_URL, json=data, timeout=5)
                logging.info("📨 Telegram update forwarded to amoCRM webhook")
        except Exception as e:
            logging.warning(f"⚠️ Failed to forward Telegram update: {e}")

    # (2) Создание сделки + контакт + файл
    if AMO_API_URL and AMO_ACCESS_TOKEN:
        try:
            msg = data.get("message") or {}
            text = msg.get("text", "")
            username = msg.get("from", {}).get("username", "unknown")
            message = f"{username}: {text}" if text else username

            # --- проверяем наличие вложения ---
            file_uuid: Optional[str] = None

            # если документ
            if "document" in msg:
                file_id = msg["document"]["file_id"]
                file_name = msg["document"].get("file_name", "file.bin")
                file_info = await bot.get_file(file_id)
                file_bytes = await bot.download_file(file_info.file_path)
                file_uuid = await upload_file_to_amo(
                    file_name, file_bytes.read()
                )

            # если фото (берём самое большое)
            elif "photo" in msg:
                photo = msg["photo"][-1]
                file_id = photo["file_id"]
                file_name = "photo.jpg"
                file_info = await bot.get_file(file_id)
                file_bytes = await bot.download_file(file_info.file_path)
                file_uuid = await upload_file_to_amo(
                    file_name, file_bytes.read()
                )

            # --- создаём контакт ---
            contact_payload = {"name": username or "Telegram user"}

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{AMO_API_URL}/api/v4/contacts",
                    headers={"Authorization": f"Bearer {AMO_ACCESS_TOKEN}"},
                    json=[contact_payload],
                ) as contact_resp:
                    if contact_resp.status == 200:
                        contact_res = await contact_resp.json()
                        contact_id = contact_res[0]["id"]
                    else:
                        contact_id = None
                        err = await contact_resp.text()
                        logging.warning(
                            f"⚠️ Contact creation failed "
                            f"[{contact_resp.status}]: {err}"
                        )

                if not contact_id:
                    logging.warning("⚠️ Contact not created, skipping lead creation")
                    return {"ok": False}

                # --- создаём сделку ---
                lead_payload = {
                    "name": f"Новый запрос из Telegram ({username})",
                    "pipeline_id": int(os.getenv("AMO_PIPELINE_ID", "0")),
                    "_embedded": {"contacts": [{"id": contact_id}]},
                }

                async with session.post(
                    f"{AMO_API_URL}/api/v4/leads",
                    headers={"Authorization": f"Bearer {AMO_ACCESS_TOKEN}"},
                    json=[lead_payload],
                ) as lead_resp:
                    # 🔁 токен устарел → пробуем обновить
                    if lead_resp.status == 401:
                        logging.warning("⚠️ amoCRM token expired — refreshing...")
                        new_token = await refresh_access_token()
                        async with session.post(
                            f"{AMO_API_URL}/api/v4/leads",
                            headers={"Authorization": f"Bearer {new_token}"},
                            json=[lead_payload],
                        ) as retry_resp:
                            if retry_resp.status == 200:
                                res = await retry_resp.json()
                                lead_id = res[0]["id"]
                                logging.info(f"✅ Lead created after token refresh: {lead_id}")
                            else:
                                err = await retry_resp.text()
                                logging.warning(
                                    f"❌ Lead creation failed after refresh [{retry_resp.status}]: {err}"
                                )

                    elif lead_resp.status == 200:
                        res = await lead_resp.json()
                        lead_id = res[0]["id"]
                        logging.info(f"✅ Created lead {lead_id} with note & file")

                    else:
                        err = await lead_resp.text()
                        logging.warning(
                            f"❌ Lead creation failed [{lead_resp.status}]: {err}"
                        )

        except Exception as e:
            logging.warning(f"⚠️ Failed to create lead in amoCRM: {e}")

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
    """Приём уведомлений от amoCRM (создание/изменение сделок)."""
    data = await request.json()
    logging.info(f"📩 Получен webhook от amoCRM: {data}")
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
