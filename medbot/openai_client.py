# medbot/openai_client.py
import os, io, asyncio, time, traceback
from typing import Optional, Tuple
from aiogram.types import Message
from aiogram import Bot
from openai import OpenAI
from storage import get_thread_id, set_thread_id
from pydub import AudioSegment

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DELAY_SEC = int(os.getenv("REPLY_DELAY_SEC", "720"))

# Админ-уведомления: либо тем же ботом в ADMIN_CHAT_ID,
# либо отдельным лог-ботом LOG_BOT_TOKEN + ADMIN_CHAT_ID
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or "0")
LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN")
_log_bot: Optional[Bot] = None

async def _notify_admin(msg: Message, text: str):
    if not ADMIN_CHAT_ID:
        return
    try:
        if LOG_BOT_TOKEN:
            global _log_bot
            if _log_bot is None:
                _log_bot = Bot(LOG_BOT_TOKEN)
            await _log_bot.send_message(ADMIN_CHAT_ID, text[:4000])
        else:
            await msg.bot.send_message(ADMIN_CHAT_ID, text[:4000])
    except Exception:
        # не мешаем основному потоку
        pass

# --- треды ---
async def ensure_thread_choice(chat_id: int, choice: str) -> bool:
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
    f = client.files.create(file=(name, io.BytesIO(data)), purpose="assistants")
    return f.id

async def _telegram_file_to_bytes(msg: Message) -> Tuple[str, bytes]:
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

def _first_text(messages) -> Optional[str]:
    for m in messages.data:
        if getattr(m, "role", None) != "assistant":
            continue
        for part in m.content:
            if part.type == "text":
                return part.text.value
    return None

# --- основная задача ---
async def schedule_processing(msg: Message, delay_sec: Optional[int] = None) -> None:
    delay = int(delay_sec if delay_sec is not None else DELAY_SEC)
    if delay > 0:
        await asyncio.sleep(delay)

    chat_id = msg.chat.id
    thread_id = get_or_create_thread(chat_id)

    try:
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

        # 4) Пулим до 10 минут, логируем статусы
        started = time.time()
        last_status = None
        while True:
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run.status != last_status:
                await _notify_admin(msg, f"[medbot] run {run.id} status={run.status}")
                last_status = run.status
            if run.status in {"completed", "failed", "requires_action", "cancelled", "expired"}:
                break
            if time.time() - started > 600:
                await _notify_admin(msg, f"[medbot] run {run.id} timeout after 600s")
                break
            await asyncio.sleep(2)

        # 5) Ответ
        if run.status == "completed":
            msgs = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=10)
            txt = _first_text(msgs)
            if txt:
                await msg.answer(txt)
                return
            await _notify_admin(msg, f"[medbot] completed but no assistant text; thread={thread_id}")

        else:
            # подробный лог ошибки
            err = getattr(run, "last_error", None)
            err_txt = f"type={getattr(err,'type',None)} msg={getattr(err,'message',None)}" if err else "no last_error"
            await _notify_admin(msg, f"[medbot] run finished with status={run.status}; {err_txt}; thread={thread_id}")

        await msg.answer("Внутренняя ошибка обработки. Пожалуйста, повторите позже.")

    except Exception as e:
        tb = traceback.format_exc(limit=10)
        await _notify_admin(msg, f"[medbot] exception: {e}\n{tb}")
        await msg.answer("Внутренняя ошибка обработки. Пожалуйста, повторите позже.")
