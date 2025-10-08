# app.py
import os  # модуль для работы с переменными окружения (секреты вне кода)
import logging  # журналирование событий
from typing import Optional, Dict, Any  # для Python 3.9
from fastapi import (  # каркас веб-приложения и утилиты
    FastAPI, Request, HTTPException, Query,
)  # перенос для flake8
from fastapi.middleware.cors import CORSMiddleware  # CORS-мидлварь
from aiogram import Bot, Dispatcher  # Bot — бот, Dispatcher — маршрутизация
from aiogram.types import Update  # тип входящих обновлений от Telegram
from dotenv import load_dotenv  # загрузка настроек из .env

from bot import setup_handlers  # noqa: E402  # регистрация хэндлеров позже
from admin_api import router as admin_router  # noqa: E402  # набор admin-роутов
from repo import fetch_messages  # 🔴 выдача сообщений для админки

load_dotenv()  # загружаем переменные окружения из .env, если есть

logging.basicConfig(  # настройка логирования
    level=logging.INFO,  # уровень логов
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",  # формат
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # секретный токен бота
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")  # секрет вебхука
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # базовый URL сервиса

bot = Bot(BOT_TOKEN)  # создаём объект Telegram-бота
dp = Dispatcher()  # создаём диспетчер aiogram

app = FastAPI(title="medbot")  # экземпляр FastAPI

ALLOWED_ORIGINS = [  # список источников, которым можно ходить к API из браузера
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://amo.ap-development.com",
]  # список доменов для CORS

app.add_middleware(  # регистрируем CORS-мидлварь
    CORSMiddleware,  # класс мидлвари
    allow_origins=ALLOWED_ORIGINS,  # кто может
    allow_credentials=True,  # разрешить куки (на будущее)
    allow_methods=["*"],  # разрешённые методы
    allow_headers=["*"],  # разрешённые заголовки
)

# webhook endpoint
@app.post("/webhook")  # куда Telegram будет слать обновления
async def telegram_webhook(request: Request) -> Dict[str, Any]:  # обработчик
    # опциональная валидация секрета
    if request.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret")  # отказ
    data = await request.json()  # читаем JSON-тело запроса
    update = Update.model_validate(data)  # парсим в объект Update
    await dp.feed_update(bot, update)  # передаём обработчикам aiogram
    return {"ok": True}  # подтверждаем приём

@app.get("/health")  # служебная проверка живости сервиса
async def health() -> Dict[str, str]:  # хэндлер health
    return {"status": "ok"}  # простой ответ

# --- запуск aiogram хэндлеров ---
setup_handlers(dp)  # регистрируем все реакции бота

# --- admin api (подключаем существующие маршруты) ---
app.include_router(admin_router)  # подключаем роутер админки


# --- НОВЫЙ ЭНДПОЙНТ: сообщения для админ-панели ---
@app.get("/admin-api/messages")  # эндпойнт списка сообщений с курсорами
async def api_messages(  # обработчик запроса сообщений
    chat_id: int = Query(..., description="ID чата (обязателен)"),  # обязателен
    limit: int = Query(20, ge=1, le=200,
                       description="Сколько сообщений вернуть"),  # лимит
    before_id: Optional[int] = Query(  # курсор «старее чем id»
        None, description="Вернуть сообщения с id < before_id",
    ),
    after_id: Optional[int] = Query(  # курсор «новее чем id»
        None, description="Вернуть сообщения с id > after_id",
    ),
    q: Optional[str] = Query(  # поиск по тексту
        None, min_length=1, description="Поиск по подстроке (ILIKE)",
    ),
    direction: Optional[int] = Query(  # 0 — user→bot, 1 — bot→user
        None, ge=0, le=1, description="Фильтр по направлению (0/1)",
    ),
    content_type: Optional[str] = Query(  # тип контента
        None, description="Тип контента: text/voice/photo/document/…",
    ),
    order: str = Query(  # порядок сортировки
        "desc", regex="^(asc|desc)$", description="Порядок сортировки",
    ),
) -> Dict[str, Any]:  # JSON-ответ со списком и метаданными
    """Возвращает порцию сообщений выбранного чата (курсорная пагинация)."""
    try:  # защита от сбоев
        data = fetch_messages(  # обращаемся к репозиторию
            chat_id=chat_id,  # идентификатор чата
            limit=limit,  # лимит выборки
            before_id=before_id,  # курсор вниз
            after_id=after_id,  # курсор вверх
            q=q,  # строка поиска
            direction=direction,  # фильтр направления
            content_type=content_type,  # фильтр по типу
            order=order,  # сортировка
        )  # получаем словарь с результатами
        return data  # отдаём как есть фронтенду
    except Exception as exc:  # перехват исключений
        raise HTTPException(status_code=500, detail=str(exc))  # единый ответ


# --- вспомогательная ручка для установки вебхука ---
@app.get("/admin/set_webhook")  # однократно после деплоя
async def set_webhook() -> Dict[str, Any]:  # установка вебхука у Telegram
    if not BASE_URL:  # проверяем, что URL задан
        raise HTTPException(500, "BASE_URL not set")  # ошибка конфигурации
    await bot.set_webhook(  # регистрируем URL вебхука
        url=f"{BASE_URL}/webhook",  # абсолютный адрес
        secret_token=WEBHOOK_SECRET,  # секрет для заголовка
        drop_pending_updates=True,  # сбрасываем очередь
    )
    return {"ok": True, "url": f"{BASE_URL}/webhook"}  # ответ
