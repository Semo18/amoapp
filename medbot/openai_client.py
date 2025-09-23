import os, io, asyncio, time, logging
from typing import Optional, Tuple
from aiogram.types import Message
from openai import OpenAI
from storage import get_thread_id, set_thread_id
from pydub import AudioSegment

log = logging.getLogger("medbot.openai")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DELAY_SEC = int(os.getenv("REPLY_DELAY_SEC", "720"))

# --- треды ---
async def ensure_thread_choice(chat_id: int, choice: str) -> bool:
    """Вернёт True, если создан новый тред; иначе False (продолжаем текущий)."""
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
    # обязательно указываем purpose для Assistants
    f = client.files.create(file=(name, io.BytesIO(data)), purpose="assistants")
    return f.id

async def _telegram_file_to_bytes(msg: Message) -> Tuple[str, bytes]:
    """Скачивает файл/фото/голос -> (filename, bytes)."""
    if getattr(msg, "voice", None):
        file = await msg.bot.get_file(msg.voice.file_id)
        b = await msg.bot.download_file(file.file_path)
        raw = b.read()
        wav = AudioSegment.from_file(io.BytesIO(raw), format="ogg")
        buf = io.BytesIO()
        wav.export(buf, format="wav")
        return "voice.wav", buf.getvalue()

    if getattr(msg, "audio", None):
        file = await msg.bot.get_file(msg.audio.file_id)
        b = await msg.bot.download_file(file.file_path)
        return (msg.audio.file_name or "audio"), b.read()

    if getattr(msg, "document", None):
        file = await msg.bot.get_file(msg.document.file_id)
        b = await msg.bot.download_file(file.file_path)
        return (msg.document.file_name or "document"), b.read()

    if getattr(msg, "photo", None):
        file = await msg.bot.get_file(msg.photo[-1].file_id)
        b = await msg.bot.download_file(file.file_path)
        return "photo.jpg", b.read()

    return "message.txt", (msg.text or "").encode("utf-8")

def _first_text_from_assistant(messages) -> Optional[str]:
    # Берём первый элемент с ролью assistant (список идёт desc)
    for m in messages.data:
        if getattr(m, "role", "") != "assistant":
            continue
        for part in m.content:
            if part.type == "text":
                return part.text.value
    return None

# --- основная задача ---
async def schedule_processing(msg: Message, delay_sec: Optional[int] = None) -> None:
    """Отложенная обработка входного сообщения через OpenAI Assistants."""
    try:
        delay = int(delay_sec if delay_sec is not None else DELAY_SEC)
        if delay > 0:
            await asyncio.sleep(delay)

        chat_id = msg.chat.id
        thread_id = get_or_create_thread(chat_id)

        # 1) Контент
        attachments = []
        content = msg.text or "Анализируй приложенный файл/сообщение."
        if any([
            getattr(msg, "voice", None),
            getattr(msg, "audio", None),
            getattr(msg, "document", None),
            getattr(msg, "photo", None),
        ]):
            name, data = await _telegram_file_to_bytes(msg)
            file_id = _upload_bytes(name, data)
            attachments = [{"file_id": file_id, "tools": [{"type": "file_search"}]}]
            content = f"Прошу учесть файл {name} при анализе. Ответь как медконсультант."

        # 2) Сообщение в тред
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=content,
            attachments=attachments or None,
        )

        # 3) Запуск run
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID,
            tool_choice="auto",
        )

        # 4) Пулинг статуса (тайм-бокс 10 минут)
        started = time.time()
        while True:
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run.status in {"completed", "failed", "requires_action", "cancelled", "expired"}:
                break
            await asyncio.sleep(2)
            if time.time() - started > 600:
                log.warning("run timeout 10m (status=%s)", run.status)
                break

        # 5) Ответ в Telegram
        if run.status == "completed":
            msgs = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=5)
            txt = _first_text_from_assistant(msgs)
            if txt:
                await msg.answer(txt)
                return

        await msg.answer("Произошла ошибка при анализе. Повторите, пожалуйста.")
    except Exception as e:
        log.exception("schedule_processing failed: %s", e)
        try:
            await msg.answer("Внутренняя ошибка обработки. Пожалуйста, повторите позже.")
        except Exception:
            pass
