# app.py
import os  # работа с переменными окружения и .env
import logging  # логирование системных событий
from typing import Optional, Dict, Any  # аннотации типов

# каркас веб-приложения и утилиты
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware  # CORS-доступ

# Telegram SDK (aiogram)
from aiogram import Bot, Dispatcher
from aiogram.types import Update

# загрузка переменных из .env
from dotenv import load_dotenv

# 🔴 используется для HTTP-запросов в amoCRM
import aiohttp

# 🔴 локальные модули проекта
from bot import setup_handlers  # регистрация Telegram-хэндлеров
from admin_api import router as admin_router  # REST для админки
from repo import fetch_messages  # получение сообщений из БД

# ======================
#     НАСТРОЙКА БАЗЫ
# ======================

load_dotenv()  # загружаем все переменные окружения из .env

# 🔴 базовая настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# --- системные параметры ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # токен Telegram-бота
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")  # секрет вебхука
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # базовый URL приложения

# --- настройки amoCRM ---
AMO_WEBHOOK_URL = os.getenv("AMO_WEBHOOK_URL", "")  # 🔴 адрес amoCRM webhook
AMO_API_URL = os.getenv("AMO_API_URL", "")  # 🔴 REST API amoCRM
AMO_ACCESS_TOKEN = os.getenv("AMO_ACCESS_TOKEN", "")  # 🔴 токен доступа API
AMO_ENABLED = bool(AMO_WEBHOOK_URL or AMO_API_URL)  # активна ли интеграция

# --- создаём объекты Telegram SDK ---
bot = Bot(BOT_TOKEN)  # основной объект Telegram-бота
dp = Dispatcher()  # маршрутизатор aiogram
app = FastAPI(title="medbot")  # приложение FastAPI

# ======================
#     НАСТРОЙКА CORS
# ======================

# Разрешаем доступ фронтенду с указанных доменов
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://amo.ap-development.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # разрешённые источники
    allow_credentials=True,  # разрешить cookie
    allow_methods=["*"],  # все HTTP-методы
    allow_headers=["*"],  # все заголовки
)

# ==========================================================
#       ГЛАВНЫЙ TELEGRAM WEBHOOK (один токен — два потока)
# ==========================================================

@app.post("/medbot/webhook")
async def telegram_webhook(request: Request) -> Dict[str, Any]:
    """
    Единая точка приёма Telegram-сообщений.
    Стратегия:
    1. Обрабатываем сообщение через aiogram (бот + OpenAI).
    2. Дублируем событие в amoCRM — webhook и/или REST API.
    3. Если в сообщении есть вложения — загружаем их в amoCRM.
    """

    # Проверяем секрет вебхука для защиты от посторонних запросов
    if request.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret")

    # Читаем тело запроса (Telegram update)
    data = await request.json()

    # Преобразуем в объект Update для aiogram
    update = Update.model_validate(data)

    # Передаём апдейт aiogram → чтобы бот обработал сообщение
    await dp.feed_update(bot, update)

    # ======================================
    # (1) Пересылаем апдейт в amoCRM webhook
    # ======================================
    if AMO_WEBHOOK_URL:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(AMO_WEBHOOK_URL, json=data, timeout=5)
                logging.info("📨 Telegram update forwarded to amoCRM webhook")
        except Exception as e:
            logging.warning(f"⚠️ Failed to forward Telegram update: {e}")

    # ======================================
    # (2) Создаём сделку + прикрепляем файл
    # ======================================
    if AMO_API_URL and AMO_ACCESS_TOKEN:
        try:
            msg = data.get("message") or {}  # безопасно достаём объект
            text = msg.get("text", "")  # текст сообщения
            username = msg.get("from", {}).get("username", "unknown")
            message = f"{username}: {text}" if text else username

            # --- проверяем, есть ли вложение ---
            file_uuid: Optional[str] = None

            # Если пользователь отправил документ
            if "document" in msg:
                file_id = msg["document"]["file_id"]
                file_name = msg["document"].get("file_name", "file.bin")

                # Скачиваем файл из Telegram
                file_info = await bot.get_file(file_id)
                file_bytes = await bot.download_file(file_info.file_path)

                # 🔴 upload_file_to_amo — отдельная функция в коде
                file_uuid = await upload_file_to_amo(file_name, file_bytes.read())

            # Если пользователь отправил фото (берём наибольшее)
            elif "photo" in msg:
                photo = msg["photo"][-1]
                file_id = photo["file_id"]
                file_name = "photo.jpg"

                file_info = await bot.get_file(file_id)
                file_bytes = await bot.download_file(file_info.file_path)
                file_uuid = await upload_file_to_amo(file_name, file_bytes.read())

            # --- создаём сделку в amoCRM ---
            lead_payload = {
                "name": f"Новый запрос из Telegram ({username})",
                "pipeline_id": int(os.getenv("AMO_PIPELINE_ID", "0")),
                "_embedded": {"contacts": [{"name": username}]},
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{AMO_API_URL}/api/v4/leads",
                    headers={"Authorization": f"Bearer {AMO_ACCESS_TOKEN}"},
                    json=[lead_payload],
                ) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        lead_id = res[0]["id"]

                        # --- создаём примечание (note) ---
                        note_payload = {
                            "note_type": "common",
                            "params": {"text": message},
                        }

                        # 🔴 если файл есть — добавляем в note
                        if file_uuid:
                            note_payload["_embedded"] = {
                                "files": [{"uuid": file_uuid}]
                            }

                        await session.post(
                            f"{AMO_API_URL}/api/v4/leads/{lead_id}/notes",
                            headers={"Authorization": f"Bearer {AMO_ACCESS_TOKEN}"},
                            json=[note_payload],
                        )

                        logging.info(f"✅ Created lead {lead_id} with note & file")
                    else:
                        err = await resp.text()
                        logging.warning(
                            f"❌ Lead creation failed [{resp.status}]: {err}"
                        )

        except Exception as e:
            logging.warning(f"⚠️ Failed to create lead in amoCRM: {e}")

    # Telegram ждёт подтверждение о приёме запроса
    return {"ok": True}


# ======================
#     HEALTHCHECK API
# ======================

@app.get("/medbot/health")
async def health() -> Dict[str, str]:
    """Служебная ручка для проверки доступности сервера."""
    return {"status": "ok"}

# =====================================================
#   ПРИЁМ СОБЫТИЙ ОТ AMOCRM (сделки, статусы, клиенты)
# =====================================================

@app.post("/medbot/amo-webhook")
async def amo_webhook(request: Request):
    """
    Принимает уведомления от amoCRM (создание/изменение сделок и т.д.).
    Стратегия: можно логировать, уведомлять врачей, вызывать OpenAI.
    """
    data = await request.json()
    logging.info(f"📩 Получен webhook от amoCRM: {data}")
    return {"ok": True}

# =====================================================
#        ADMIN API, TELEGRAM ХЭНДЛЕРЫ, WEBHOOK SETUP
# =====================================================

# 🔹 Регистрируем Telegram-хэндлеры
setup_handlers(dp)

# 🔹 Подключаем REST API админки
app.include_router(admin_router)

@app.get("/admin/set_webhook")
async def set_webhook() -> Dict[str, Any]:
    """
    Устанавливает Telegram webhook на наш сервер.
    Нужно вызвать один раз после деплоя.
    """
    if not BASE_URL:
        raise HTTPException(500, "BASE_URL not set")

    await bot.set_webhook(
        url=f"{BASE_URL}/medbot/webhook",
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )
    return {"ok": True, "url": f"{BASE_URL}/medbot/webhook"}

# =====================================================
#   СООБЩЕНИЯ ДЛЯ АДМИН-ПАНЕЛИ (ПАГИНАЦИЯ, ПОИСК)
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
    """API для фронта админ-панели: выдача сообщений с фильтрами."""
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
# Tes2222