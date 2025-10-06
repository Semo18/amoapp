# app.py
import os  # модуль для работы с переменными окружения (хранение секретов вне кода)
from fastapi import FastAPI, Request, HTTPException  # каркас веб-приложения и инструменты для обработки запросов/ошибок
from aiogram import Bot, Dispatcher  # библиотека для работы с Telegram-ботом: Bot — сам бот, Dispatcher — распределитель событий
from aiogram.types import Update  # тип данных для входящих событий от Telegram (сообщений и др.)
import logging  # журналирование событий (логи)
from dotenv import load_dotenv  # загрузка настроек и секретов из файла .env

load_dotenv()  # загружаем переменные окружения из файла .env, если он есть

logging.basicConfig(  # настройка логирования
    level=logging.INFO,  # уровень логов — писать служебные сообщения и выше
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"  # шаблон отображения лога: время, уровень, имя, сообщение
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # токен бота, полученный от BotFather (секретный ключ)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")  # секрет для проверки запросов на вебхук (по умолчанию "secret")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # адрес сервера, где крутится бот (обрезаем лишний "/")

bot = Bot(BOT_TOKEN)  # создаём объект бота с токеном
dp = Dispatcher()  # создаём диспетчер для распределения событий по обработчикам

app = FastAPI(title="medbot")  # создаём веб-приложение FastAPI с названием "medbot"

# webhook endpoint
@app.post("/webhook")  # адрес, куда Telegram будет слать события (например, новые сообщения)
async def telegram_webhook(request: Request):  # функция обработки входящих событий
    # опциональная валидация секрета
    if request.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:  # проверяем секретный токен в заголовке запроса
        raise HTTPException(status_code=403, detail="bad secret")  # если секрет не совпал — ошибка "Доступ запрещён"
    data = await request.json()  # читаем тело запроса в формате JSON
    update = Update.model_validate(data)  # превращаем JSON в объект Update (событие от Telegram)
    await dp.feed_update(bot, update)  # передаём событие диспетчеру для обработки
    return {"ok": True}  # возвращаем ответ "всё ок"

@app.get("/health")  # служебный адрес для проверки — жив ли сервис
async def health():
    return {"status": "ok"}  # возвращаем простое подтверждение

# --- запуск aiogram хэндлеров ---
from bot import setup_handlers  # noqa  # подключаем функцию, где описаны все реакции бота на события
setup_handlers(dp)  # регистрируем обработчики событий в диспетчере

# --- admin api ---
from admin_api import router as admin_router  # noqa
app.include_router(admin_router)

# вспомогательная ручка для установки вебхука (однократно после деплоя)
@app.get("/admin/set_webhook")  # отдельный адрес, чтобы вручную задать вебхук после запуска
async def set_webhook():
    if not BASE_URL:  # если не задан адрес сервера
        raise HTTPException(500, "BASE_URL not set")  # возвращаем ошибку
    await bot.set_webhook(  # настраиваем вебхук у Telegram
        url=f"{BASE_URL}/webhook",  # куда именно Telegram будет присылать события
        secret_token=WEBHOOK_SECRET,  # какой секрет использовать для проверки
        drop_pending_updates=True  # при установке удалить все старые необработанные обновления
    )
    return {"ok": True, "url": f"{BASE_URL}/webhook"}  # возвращаем ответ с подтверждением и ссылкой вебхука
