import os
from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

app = FastAPI(title="medbot")

# webhook endpoint
@app.post("/webhook")
async def telegram_webhook(request: Request):
    # опциональная валидация секрета
    if request.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret")
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok"}

# --- запуск aiogram хэндлеров ---
from bot import setup_handlers  # noqa
setup_handlers(dp)

# вспомогательная ручка для установки вебхука (однократно после деплоя)
@app.get("/admin/set_webhook")
async def set_webhook():
    if not BASE_URL:
        raise HTTPException(500, "BASE_URL not set")
    await bot.set_webhook(
        url=f"{BASE_URL}/webhook",
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True
    )
    return {"ok": True, "url": f"{BASE_URL}/webhook"}
