import os, io, asyncio, time
from typing import Optional
from aiogram.types import Message
from openai import OpenAI
from storage import get_thread_id, set_thread_id, drop_thread_id
from pydub import AudioSegment

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
VECTOR_STORE_ID = os.getenv("VECTOR_STORE_ID")

# --- треды ---
async def ensure_thread_choice(chat_id: int, choice: str) -> bool:
    """Возвращает True если создан новый тред."""
    if choice == "новый":
        th = client.beta.threads.create()
        set_thread_id(chat_id, th.id)
        return True
    return False

def get_or_create_thread(chat_id: int) -> str:
    th = get_thread_id(chat_id)
    if th:
        return th
    th_obj = client.beta.threads.create()
    set_thread_id(chat_id, th_obj.id)
    return th_obj.id

# --- файлы ---

def _upload_bytes(name: str, data: bytes) -> str:
    f = client.files.create(file=(name, io.BytesIO(data)))
    return f.id

async def _telegram_file_to_bytes(msg: Message) -> tuple[str, bytes]:
    """Скачивает файл/фото/голос -> (filename, bytes)."""
    if msg.voice:
        # voice(OGG/Opus) -> WAV
        file = await msg.bot.get_file(msg.voice.file_id)
        b = await msg.bot.download_file(file.file_path)
        raw = b.read()
        wav = AudioSegment.from_file(io.BytesIO(raw), format="ogg")
        buf = io.BytesIO()
        wav.export(buf, format="wav")
        return ("voice.wav", buf.getvalue())
    if msg.audio:
        file = await msg.bot.get_file(msg.audio.file_id)
        b = await msg.bot.download_file(file.file_path)
        return (msg.audio.file_name or "audio", b.read())
    if msg.document:
        file = await msg.bot.get_file(msg.document.file_id)
        b = await msg.bot.download_file(file.file_path)
        return (msg.document.file_name or "document", b.read())
    if msg.photo:
        file = await msg.bot.get_file(msg.photo[-1].file_id)
        b = await msg.bot.download_file(file.file_path)
        return ("photo.jpg", b.read())
    return ("message.txt", (msg.text or "").encode("utf-8"))

# --- основная задача ---

async def schedule_processing(msg: Message, delay_sec: int = 600):
    """Отложенная обработка входа через OpenAI Assistants."""
    await asyncio.sleep(delay_sec)
    chat_id = msg.chat.id
    thread_id = get_or_create_thread(chat_id)

    # 1) Готовим контент: если файл/голос — загружаем как file; иначе текст
    attachments = []
    content = msg.text or "Анализируй приложенный файл/сообщение."
    if any([msg.voice, msg.audio, msg.document, msg.photo]):
        name, data = await _telegram_file_to_bytes(msg)
        file_id = _upload_bytes(name, data)
        attachments = [{"file_id": file_id, "tools": [{"type": "file_search"}]}]
        content = f"Прошу учесть файл {name} при анализе. Ответь как медконсультант."

    # 2) Создаём сообщение в треде
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=content,
        attachments=attachments or None
    )

    # 3) Запускаем ран ассистента
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        tool_choice="auto"
    )

    # 4) Пулинг до готовности
    started = time.time()
    while True:
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if run.status in ("completed", "failed", "requires_action", "cancelled", "expired"):
            break
        await asyncio.sleep(2)

    # 5) Отдаём ответ в Telegram
    if run.status == "completed":
        msgs = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=1)
        txt = _first_text(msgs)
        if txt:
            await msg.answer(txt)
            return
    await msg.answer("Произошла ошибка при анализе. Повторите, пожалуйста.")

def _first_text(messages) -> Optional[str]:
    for m in messages.data:
        for part in m.content:
            if part.type == "text":
                return part.text.value
    return None
