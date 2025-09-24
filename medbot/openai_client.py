# medbot/openai_client.py
import os, io, asyncio, time, mimetypes, logging
from typing import Optional, Tuple, Literal
from aiogram.types import Message
from openai import OpenAI
from storage import get_thread_id, set_thread_id
from pydub import AudioSegment

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DELAY_SEC = int(os.getenv("REPLY_DELAY_SEC", "0"))

# ---------- helpers ----------
def _ext(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()

FILE_SEARCH_EXTS = {
    ".pdf", ".txt", ".md", ".doc", ".docx", ".rtf",
    ".xls", ".xlsx", ".csv", ".tsv",
    ".ppt", ".pptx",
    ".json", ".html", ".htm", ".xml"
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".oga", ".m4a", ".flac", ".amr"}

Kind = Literal["image", "audio", "doc", "text"]

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

# --- файлы / загрузка ---
def _upload_bytes(name: str, data: bytes) -> str:
    f = client.files.create(
        file=(name, io.BytesIO(data)),
        purpose="assistants",       # <— это критично
    )
    return f.id

async def _telegram_file_to_bytes(msg: Message) -> Tuple[str, bytes, Kind]:
    """
    Возвращает (filename, data, kind)
    kind ∈ {'image','audio','doc','text'}
    """
    if getattr(msg, "voice", None):
        # voice OGG/Opus -> WAV (для распознавания/унификации)
        file = await msg.bot.get_file(msg.voice.file_id)
        b = await msg.bot.download_file(file.file_path)
        raw = b.read()
        wav = AudioSegment.from_file(io.BytesIO(raw), format="ogg")
        buf = io.BytesIO()
        wav.export(buf, format="wav")
        return "voice.wav", buf.getvalue(), "audio"

    if getattr(msg, "audio", None):
        file = await msg.bot.get_file(msg.audio.file_id)
        b = await msg.bot.download_file(file.file_path)
        name = msg.audio.file_name or "audio" + (_ext(msg.audio.file_name or "") or ".mp3")
        return name, b.read(), "audio"

    if getattr(msg, "photo", None):
        file = await msg.bot.get_file(msg.photo[-1].file_id)
        b = await msg.bot.download_file(file.file_path)
        return "photo.jpg", b.read(), "image"

    if getattr(msg, "document", None):
        file = await msg.bot.get_file(msg.document.file_id)
        b = await msg.bot.download_file(file.file_path)
        name = msg.document.file_name or "document"
        ext = _ext(name)
        mime = (msg.document.mime_type or mimetypes.types_map.get(ext, "")).lower()
        if ext in IMAGE_EXTS or mime.startswith("image/"):
            return name or "image.jpg", b.read(), "image"
        elif ext in AUDIO_EXTS or mime.startswith("audio/"):
            return name or "audio.wav", b.read(), "audio"
        else:
            return name or "document.bin", b.read(), "doc"

    # текстовое сообщение
    return "message.txt", (msg.text or "").encode("utf-8"), "text"

def _first_text(messages) -> Optional[str]:
    for m in messages.data:
        if getattr(m, "role", None) == "assistant":
            for part in m.content:
                if part.type == "text":
                    return part.text.value
    return None

# --- основная задача ---
async def schedule_processing(msg: Message, delay_sec: Optional[int] = None) -> None:
    """Отложенная обработка входного сообщения через OpenAI Assistants."""
    delay = int(delay_sec if delay_sec is not None else DELAY_SEC)
    if delay > 0:
        await asyncio.sleep(delay)

    chat_id = msg.chat.id
    thread_id = get_or_create_thread(chat_id)
    logging.info(f"[medbot] start processing chat_id={chat_id} thread_id={thread_id}")


    # 1) Подготовка контента
    content = None        # список частей для content=[...]
    attachments = None    # только для file_search документов

    if any([
        getattr(msg, "voice", None),
        getattr(msg, "audio", None),
        getattr(msg, "document", None),
        getattr(msg, "photo", None),
    ]):
        name, data, kind = await _telegram_file_to_bytes(msg)
        ext = _ext(name)

        if kind == "image":
            # Картинки — как input_image, НЕ через file_search
            file_id = _upload_bytes(name, data)
            content = [
                {"type": "input_text",
                 "text": f"Проанализируй изображение {name}. Дай медицинский комментарий и рекомендации."},
                {"type": "input_image", "image_file": {"file_id": file_id}},
            ]

        elif kind == "audio":
            # Аудио — сначала транскрибируем
            try:
                tr = client.audio.transcriptions.create(
                    model="whisper-1",  # доступная и надёжная модель распознавания
                    file=(name, io.BytesIO(data)),
                )
                text = tr.text.strip() if getattr(tr, "text", None) else ""
            except Exception as e:
                text = ""
            if not text:
                text = "Не удалось автоматически распознать голосовое сообщение."
            content = [
                {"type": "input_text",
                 "text": f"Расшифровка голосового ({name}):\n{text}\n\nОтветь как медицинский консультант."}
            ]

        elif kind == "doc" and ext in FILE_SEARCH_EXTS:
            # Документ — через file_search
            file_id = _upload_bytes(name, data)
            attachments = [{"file_id": file_id, "tools": [{"type": "file_search"}]}]
            content = [{
                "type": "input_text",
                "text": f"Прошу учесть документ {name} при анализе. Ответь как медконсультант."
            }]

        else:
            # Неподдерживаемый файл — просто текстовая подсказка без вложения
            content = [{
                "type": "input_text",
                "text": f"Пользователь прикрепил файл {name}, который нельзя проиндексировать. "
                        f"Ответь по описанию от пользователя и скажи, какие форматы подходят: PDF/DOCX/TXT/CSV/XLSX/PPTX/HTML."
            }]

    else:
        # чистый текст
        content = [{"type": "input_text", "text": msg.text or "Опиши симптомы и приложи анализы."}]

    # 2) Сообщение в тред
    kwargs = dict(thread_id=thread_id, role="user", content=content)
    if attachments:
        kwargs["attachments"] = attachments
    client.beta.threads.messages.create(**kwargs)

    # 3) Запускаем ран ассистента
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        tool_choice="auto",
    )

    # 4) Ожидание результата (тайм-бокс 10 минут)
    started = time.time()
    while True:
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if run.status in {"completed", "failed", "requires_action", "cancelled", "expired"}:
            break
        await asyncio.sleep(2)
        if time.time() - started > 600:
            break

    logging.info(f"[medbot] run status={run.status} chat_id={chat_id}")

    # 5) Ответ в Telegram
    if run.status == "completed":
        msgs = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=3)
        txt = _first_text(msgs)
        if txt:
            await msg.answer(txt)
            return

    await msg.answer("Внутренняя ошибка обработки. Пожалуйста, попробуйте позже.")
