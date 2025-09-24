import os, io, asyncio, time, pathlib, traceback
from typing import Optional, Tuple, List, Dict, Any
from aiogram.types import Message
from aiogram import Bot  # <— добавили
from openai import OpenAI
from storage import get_thread_id, set_thread_id
from pydub import AudioSegment

# --- конфиг ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DELAY_SEC = int(os.getenv("REPLY_DELAY_SEC", "0"))

# логирование: поддержка двух режимов
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID", "") or os.getenv("ADMIN_CHAT_ID", "")
LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN", "")
LOG_PREFIX = "[medbot]"

# если задан отдельный токен для логов — поднимем отдельного бота один раз
_log_bot: Optional[Bot] = Bot(LOG_BOT_TOKEN) if LOG_BOT_TOKEN else None

# --- поддержка типов ---
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
RETRIEVAL_EXTS = {".pdf", ".txt", ".md", ".csv", ".docx", ".pptx", ".xlsx", ".json", ".rtf", ".html", ".htm"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".opus"}

def _ext(name: str) -> str:
    return pathlib.Path(name).suffix.lower()

def _is_image(name: str) -> bool:
    return _ext(name) in IMAGE_EXTS

def _is_audio(name: str) -> bool:
    return _ext(name) in AUDIO_EXTS

def _is_retrieval_doc(name: str) -> bool:
    return _ext(name) in RETRIEVAL_EXTS

# --- util: лог в служебный чат ---
async def send_log(runtime_bot: Bot, text: str) -> None:
    """Отправляет сообщение в лог-чат.
    - Если есть LOG_BOT_TOKEN — шлём через отдельного бота (_log_bot)
    - Иначе — через runtime_bot (основной бот)
    - chat_id = LOG_CHAT_ID или ADMIN_CHAT_ID
    """
    if not LOG_CHAT_ID:
        return
    try:
        bot = _log_bot or runtime_bot
        await bot.send_message(LOG_CHAT_ID, f"{LOG_PREFIX} {text}")
    except Exception:
        # не падаем на логах
        pass

# --- треды и файлы — без изменений ---
def get_or_create_thread(chat_id: int) -> str:
    th = get_thread_id(chat_id)
    if th:
        return th
    th_obj = client.beta.threads.create()
    set_thread_id(chat_id, th_obj.id)
    return th_obj.id

def _upload_bytes(name: str, data: bytes) -> str:
    return client.files.create(file=(name, io.BytesIO(data)), purpose="assistants").id

async def _telegram_file_to_bytes(msg: Message) -> Tuple[str, bytes]:
    if getattr(msg, "voice", None):
        f = await msg.bot.get_file(msg.voice.file_id)
        b = await msg.bot.download_file(f.file_path)
        raw = b.read()
        wav = AudioSegment.from_file(io.BytesIO(raw))
        buf = io.BytesIO()
        wav.export(buf, format="wav")
        return "voice.wav", buf.getvalue()
    if getattr(msg, "audio", None):
        f = await msg.bot.get_file(msg.audio.file_id)
        b = await msg.bot.download_file(f.file_path)
        return (msg.audio.file_name or "audio.mp3"), b.read()
    if getattr(msg, "document", None):
        f = await msg.bot.get_file(msg.document.file_id)
        b = await msg.bot.download_file(f.file_path)
        return (msg.document.file_name or "document"), b.read()
    if getattr(msg, "photo", None):
        f = await msg.bot.get_file(msg.photo[-1].file_id)
        b = await msg.bot.download_file(f.file_path)
        return "photo.jpg", b.read()
    return "message.txt", (msg.text or "").encode("utf-8")

def _first_text(messages) -> Optional[str]:
    for m in messages.data:
        if getattr(m, "role", None) == "assistant":
            for part in m.content:
                if part.type == "text":
                    return part.text.value
    return None

# --- основная задача ---
async def schedule_processing(msg: Message, delay_sec: Optional[int] = None) -> None:
    try:
        delay = int(delay_sec if delay_sec is not None else DELAY_SEC)
        if delay > 0:
            await asyncio.sleep(delay)

        chat_id = msg.chat.id
        thread_id = get_or_create_thread(chat_id)

        base_text = msg.text or "Проанализируй вложение и ответь как медицинский консультант."

        content: List[Dict[str, Any]] = [{"type": "text", "text": base_text}]
        attachments = None

        if any([getattr(msg, "voice", None),
                getattr(msg, "audio", None),
                getattr(msg, "document", None),
                getattr(msg, "photo", None)]):

            name, data = await _telegram_file_to_bytes(msg)
            fid = _upload_bytes(name, data)

            if _is_image(name):
                content.append({"type": "image_file", "image_file": {"file_id": fid}})

            elif _is_audio(name):
                # транскрипция через Whisper
                try:
                    tr = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=(name, io.BytesIO(data)),
                    )
                    text = tr.text.strip() if getattr(tr, "text", None) else ""
                except Exception:
                    text = ""
                if not text:
                    text = "Не удалось автоматически распознать голосовое сообщение."
                content = [{"type": "text",
                            "text": f"Расшифровка голосового ({name}):\n{text}\n\nОтветь как медицинский консультант."}]

            elif _is_retrieval_doc(name):
                attachments = [{"file_id": fid, "tools": [{"type": "file_search"}]}]
                content[0]["text"] = f"{base_text}\n\nУчти документ: {name}"

            else:
                content[0]["text"] = f"{base_text}\n\n(Файл {name} загружен; если нужно, укажите правильный формат: PDF/JPG и т.п.)"

        # 2) сообщение в тред
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=content,
            attachments=attachments,
        )

        # 3) запуск run
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID,
            tool_choice="auto",
        )

        # 4) мониторинг статуса (логируем смену статуса в лог-чат)
        started = time.time()
        last_status = None
        while True:
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run.status != last_status:
                await send_log(msg.bot, f"run {run.id} status={run.status} chat_id={chat_id}")
                last_status = run.status
            if run.status in {"completed", "failed", "requires_action", "cancelled", "expired"}:
                break
            await asyncio.sleep(2)
            if time.time() - started > 600:
                await send_log(msg.bot, f"run {run.id} timeout chat_id={chat_id}")
                break

        # 5) ответ пользователю
        if run.status == "completed":
            msgs = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=2)
            txt = _first_text(msgs)
            if txt:
                await msg.answer(txt)
                return

        await msg.answer("Внутренняя ошибка обработки. Пожалуйста, повторите позже.")
        await send_log(msg.bot, f"run {run.id} finished with status={run.status} (no text) chat_id={chat_id}")

    except Exception as e:
        await msg.answer("Внутренняя ошибка обработки. Пожалуйста, повторите позже.")
        await send_log(msg.bot, f"exception: {e}\n{traceback.format_exc()}")
