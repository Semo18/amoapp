import asyncio
from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from texts import WELCOME, DISCLAIMER, ACK_DELAYED
from openai_client import schedule_processing, ensure_thread_choice

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
    # выбор: продолжать или новый тред
    text = ("Хотите продолжить текущий медицинский диалог или начать новый?\n"
            "• Напишите: «продолжить» — чтобы общаться в текущем треде\n"
            "• Напишите: «новый» — чтобы создать новый тред")
    await msg.answer(text)

@router.message(F.text.lower().in_({"продолжить", "новый"}))
async def on_thread_choice(msg: Message):
    created = await ensure_thread_choice(msg.chat.id, msg.text.lower())
    await msg.answer("Готово. " + ("Создан новый тред." if created else "Продолжаем текущий тред."))

# Любое сообщение/файл: мгновенно шлём «врач читает…» и ставим отложенную задачу
@router.message()
async def any_message(msg: Message, bot: Bot):
    await msg.answer(ACK_DELAYED)
    # Отложенная обработка 10–15 минут (выбери 12*60 как среднее)
    await schedule_processing(msg, delay_sec=12*60)
