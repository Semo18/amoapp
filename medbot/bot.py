import os
import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.enums import ChatAction

from storage import ack_once
from texts import WELCOME, DISCLAIMER, ACK_DELAYED  # ACK_TEXT заменил на ACK_DELAYED
from openai_client import schedule_processing, ensure_thread_choice

# На время разработки 60 сек; можно переопределить в .env -> REPLY_DELAY_SEC
DELAY_SEC = int(os.getenv("REPLY_DELAY_SEC", "60"))

router = Router()

def setup_handlers(dp):
    dp.include_router(router)

@router.message(CommandStart())
async def cmd_start(msg: Message):
    await msg.answer(WELCOME)
    await asyncio.sleep(0.3)
    await msg.answer(DISCLAIMER, disable_web_page_preview=True)

@router.message(Command("new"))
async def cmd_new(msg: Message):
    text = (
        "Хотите продолжить текущий медицинский диалог или начать новый?\n"
        "• Напишите: «продолжить» — чтобы общаться в текущем треде\n"
        "• Напишите: «новый» — чтобы создать новый тред"
    )
    await msg.answer(text)

@router.message(F.text.lower().in_({"продолжить", "новый"}))
async def on_thread_choice(msg: Message):
    created = await ensure_thread_choice(msg.chat.id, msg.text.lower())
    await msg.answer("Готово. " + ("Создан новый тред." if created else "Продолжаем текущий тред."))

# --- утилита для "трёх точек" ---
async def _typing_later(bot: Bot, chat_id: int, start_in: int, duration: int = 60):
    """Показываем ChatAction.TYPING начиная через start_in сек и ~duration сек поддерживаем индикатор."""
    await asyncio.sleep(max(0, start_in))
    loop = asyncio.get_event_loop()
    until = loop.time() + max(1, duration)
    while loop.time() < until:
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:
            pass
        await asyncio.sleep(4)  # Telegram держит индикатор ~5 сек

# Любое входящее: ОДИН раз даём авто-квиток, ставим обработку и показываем typing перед ответом
@router.message()
async def any_message(msg: Message, bot: Bot):
    chat_id = msg.chat.id

    # 1) авто-квиток — только один раз на чат (TTL по умолчанию 24ч)
    if ack_once(chat_id):
        await msg.answer(ACK_DELAYED)

    # 2) "три точки" за минуту до ответа (или сразу, если задержка < 60)
    start_in = max(0, DELAY_SEC - 60)
    typing_duration = min(60, DELAY_SEC)
    asyncio.create_task(_typing_later(bot, chat_id, start_in, typing_duration))

    # 3) запускаем обработку В ФОНЕ (не блокируем хендлер)
    asyncio.create_task(schedule_processing(msg, delay_sec=DELAY_SEC))
