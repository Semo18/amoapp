# openai_client.py
import os, io, asyncio, time, traceback  # стандартные модули: работа с окружением/потоками байт/асинхронностью/временем/путями/трассировкой ошибок
import re  # для очистки/нормализации Markdown-разметки
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional, Tuple, List, Dict, Any  # подсказки типов для читаемости и IDE
from aiogram.types import Message  # тип входящего сообщения из Telegram
from aiogram import Bot  # объект Telegram-бота (чтобы отправлять сообщения/действия)
from aiogram.enums import ChatAction  # понадобится для отправки индикатора "печатает..."
from openai import OpenAI  # официальный клиент OpenAI API
from storage import get_thread_id, set_thread_id  # функции сохранения/чтения ID треда (сессии) по chat_id
from pydub import AudioSegment  # библиотека для работы со звуком (конвертации аудио)
from repo import save_message  # функция записи сообщений в БД
from texts import ACK_DELAYED  # 🔴 стандартное сообщение-врач (из texts.py)
from storage import should_ack
import logging  # 🔴 добавить наверху, если нет


# Загружаем .env из каталога medbot (где лежит этот файл)
DOTENV_PATH = Path(__file__).resolve().parent / ".env"  # путь к .env рядом с кодом
load_dotenv(dotenv_path=DOTENV_PATH)  # подхватываем секреты из .env

# Безопасно читаем ключ и валидируем наличие
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # читаем ключ OpenAI
if not OPENAI_API_KEY:  # если ключа нет — даём явную ошибку с подсказкой
    raise RuntimeError(
        f"OPENAI_API_KEY is not set. Expected in {DOTENV_PATH}. "
        "Add line: OPENAI_API_KEY=sk-... to your medbot/.env"
    )

# Попытка подключить Redis для межпроцессного лока; если не установлен, используем in-memory локи
try:
    # redis-py 5.x
    import redis.asyncio as aioredis  # асинхронный Redis-клиент
except Exception:
    try:
        # redis-py 4.x стиль
        from redis import asyncio as aioredis  # альтернатива импорту
    except Exception:
        aioredis = None  # фоллбек: будем использовать локи в памяти процесса

# --- конфиг ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # создаём клиент OpenAI с ключом из переменных окружения
ASSISTANT_ID = os.getenv("ASSISTANT_ID")  # ID настроенного ассистента в OpenAI (Assistant API)
DELAY_SEC = int(os.getenv("REPLY_DELAY_SEC", "0"))  # базовая задержка ответа (сек), по умолчанию 0

# логирование: поддержка двух режимов
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID", "") or os.getenv("ADMIN_CHAT_ID", "")  # чат для служебных логов (если задан)
LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN", "")  # отдельный токен бота для логов (если хотим слать логи не основным ботом)
LOG_PREFIX = "[medbot]"  # префикс для сообщений в лог-чат

# если задан отдельный токен для логов — поднимем отдельного бота один раз
_log_bot: Optional[Bot] = Bot(LOG_BOT_TOKEN) if LOG_BOT_TOKEN else None  # создаём «бота для логов» или оставляем None

# Redis-клиент (опционально) и in-memory локи
REDIS_URL = os.getenv("REDIS_URL", "")  # строка подключения к Redis (если задана)
_redis = aioredis.from_url(REDIS_URL, decode_responses=True) if (aioredis and REDIS_URL) else None  # клиент или None
_local_locks: Dict[str, asyncio.Lock] = {}  # локи в памяти по thread_id


# --- поддержка типов ---
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}  # расширения, которые считаем изображениями
RETRIEVAL_EXTS = {".pdf", ".txt", ".md", ".csv", ".docx", ".pptx", ".xlsx", ".json", ".rtf", ".html", ".htm"}  # документы для поиска по файлам
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".opus"}  # распространённые аудиоформаты

# статусы «активного» run — при них нельзя добавлять новые сообщения в тред
_ACTIVE_RUN_STATUSES = {"queued", "in_progress", "requires_action", "cancelling"}  # набор активных статусов


# 🔴 хелпер: красиво логировать ошибки Run
def _log_run_error(run) -> None:
    """Если Run упал, логируем last_error с типом/сообщением."""
    try:
        err = getattr(run, "last_error", None)
        if err:
            tp = getattr(err, "type", "unknown")
            msg = getattr(err, "message", "")
            logging.error("OpenAI run error: type=%s msg=%s", tp, msg)
    except Exception:
        pass  # логирование не должно валить поток
# ---------- УТИЛИТЫ ДЛЯ ТАЙПИНГА И ОЧИСТКИ/НАРЕЗКИ ОТВЕТОВ ----------


async def _typing_for(bot: Bot, chat_id: int, seconds: float) -> None:  # показывает "печатает..." непрерывно N секунд
    """Поддерживаем индикатор печати нужное время, отправляя ChatAction.TYPING раз в ~4 сек."""
    end_at = time.time() + max(0.0, seconds)  # когда прекратить
    while time.time() < end_at:  # пока не вышло время
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)  # показать "печатает..."
        except Exception:
            pass  # не роняем обработку при сетевых/транзиентных ошибках
        await asyncio.sleep(4)  # Telegram держит индикатор около 5 сек; обновляем каждые ~4 сек

_MD_STRIP_PATTERNS = [  # шаблоны Markdown, которые нужно скрыть от пользователя
    (r"\*{2}(.+?)\*{2}", r"\1"),   # **жирный** → жирный (без **)
    (r"#{1,6}\s*", ""),            # заголовки вида ### Title → Title
    (r"^-{3,}\s*$", ""),           # --- (горизонтальная линия) → удалить
    (r"`{3}.*?`{3}", ""),          # код-блок ```...``` → удалить содержимое (простое поведение)
    (r"`([^`]+)`", r"\1"),         # инлайн-код `x` → x
]

def _sanitize_markdown(text: str) -> str:  # удаляет/облегчает Markdown-разметку
    s = text or ""  # безопасно работаем с None
    for pat, repl in _MD_STRIP_PATTERNS:  # прогон по шаблонам
        s = re.sub(pat, repl, s, flags=re.MULTILINE | re.DOTALL)  # замена по всему тексту
    return s.strip()  # финальная нормализация пробелов/краёв

def _split_for_delivery(text: str) -> List[str]:
    """
    Режет длинный текст на части, не разрывая предложения.
    Приблизительно: 1500 / 2500 / остаток.
    Если подходящей точки не найдено, делит по лимиту.
    """
    t = text.strip() if text else ""
    if not t:
        return []

    def _safe_cut(segment: str, limit: int) -> str:
        """🔴 Ищет ближайший конец предложения перед лимитом."""
        if len(segment) <= limit:
            return segment
        cut = segment[:limit]
        # ищем последнюю "границу предложения"
        for p in [". ", "! ", "? ", "\n"]:
            pos = cut.rfind(p)
            if pos != -1 and pos > limit * 0.6:  # если нашли и не слишком рано
                return cut[: pos + len(p)].strip()
        return cut.strip()  # fallback — просто обрезаем

    parts: List[str] = []
    remaining = t

    # 🔴 Первая часть ~1500 символов (по предложению)
    first = _safe_cut(remaining, 1500)
    parts.append(first)
    remaining = remaining[len(first):].strip()

    # 🔴 Вторая часть ~2500 символов (по предложению)
    if remaining:
        second = _safe_cut(remaining, 2500)
        parts.append(second)
        remaining = remaining[len(second):].strip()

    # 🔴 Остаток разбиваем кусками по 4096 (Telegram limit)
    while remaining:
        chunk = _safe_cut(remaining, 4096)
        parts.append(chunk)
        remaining = remaining[len(chunk):].strip()

    return [p for p in parts if p]


# --- треды ---
async def ensure_thread_choice(chat_id: int, choice: str) -> bool:  # проверяет выбор пользователя: «новый»/«продолжить»
    """
    Если пользователь пишет "новый", создаём новый thread в OpenAI
    и сохраняем его ID в Redis. Возвращает True, если создан новый тред.
    """
    if choice == "новый":  # если пользователь выбрал начать новый диалог
        th = client.beta.threads.create()  # создаём новый тред (сессию) в OpenAI
        set_thread_id(chat_id, th.id)  # сохраняем ID треда для этого чата
        return True  # сообщаем, что тред был создан
    return False  # иначе — ничего не создавали

def get_or_create_thread(chat_id: int) -> str:  # возвращает существующий тред или создаёт новый
    """
    Возвращает существующий thread_id для чата,
    либо создаёт новый, если его нет.
    """
    th = get_thread_id(chat_id)  # пытаемся взять сохранённый thread_id из хранилища
    if th:  # если найден
        return th  # возвращаем его
    th_obj = client.beta.threads.create()  # иначе создаём новый тред в OpenAI
    set_thread_id(chat_id, th_obj.id)  # сохраняем новый ID
    return th_obj.id  # и возвращаем его

def _ext(name: str) -> str:  # утилита: получить расширение файла
    return pathlib.Path(name).suffix.lower()  # берём суффикс (расширение) и приводим к нижнему регистру

def _is_image(name: str) -> bool:  # проверка: это изображение?
    return _ext(name) in IMAGE_EXTS  # да, если расширение в списке IMAGE_EXTS

def _is_audio(name: str) -> bool:  # проверка: это аудио?
    return _ext(name) in AUDIO_EXTS  # да, если расширение в списке AUDIO_EXTS

def _is_retrieval_doc(name: str) -> bool:  # проверка: это документ для «поиска по файлам»?
    return _ext(name) in RETRIEVAL_EXTS  # да, если расширение в списке RETRIEVAL_EXTS

# --- util: лог в служебный чат ---
async def send_log(runtime_bot: Bot, text: str) -> None:  # отправляет строку лога в служебный Telegram-чат
    """Отправляет сообщение в лог-чат.
    - Если есть LOG_BOT_TOKEN — шлём через отдельного бота (_log_bot)
    - Иначе — через runtime_bot (основной бот)
    - chat_id = LOG_CHAT_ID или ADMIN_CHAT_ID
    """
    if not LOG_CHAT_ID:  # если лог-чат не задан — ничего не делаем
        return
    try:
        bot = _log_bot or runtime_bot  # выбираем бота: отдельного для логов или текущего
        await bot.send_message(LOG_CHAT_ID, f"{LOG_PREFIX} {text}")  # отправляем сообщение в лог-чат
    except Exception:  # любые ошибки логирования не должны ломать основную логику
        # не падаем на логах
        pass

def _upload_bytes(name: str, data: bytes) -> str:  # загрузка произвольного файла в OpenAI и возврат его file_id
    return client.files.create(file=(name, io.BytesIO(data)), purpose="assistants").id  # создаём файл в OpenAI под ассистентов

async def _telegram_file_to_bytes(msg: Message) -> Tuple[str, bytes]:  # скачивает файл из Telegram и возвращает (имя, байты)
    if getattr(msg, "voice", None):  # если это голосовое сообщение
        f = await msg.bot.get_file(msg.voice.file_id)  # получаем метаданные файла
        b = await msg.bot.download_file(f.file_path)  # скачиваем содержимое
        raw = b.read()  # читаем байты
        wav = AudioSegment.from_file(io.BytesIO(raw))  # открываем аудио с авто-определением формата
        buf = io.BytesIO()  # создаём буфер в памяти
        wav.export(buf, format="wav")  # конвертируем в WAV (удобно для распознавания)
        return "voice.wav", buf.getvalue()  # возвращаем имя и байты WAV
    if getattr(msg, "audio", None):  # если это обычный аудиофайл
        f = await msg.bot.get_file(msg.audio.file_id)  # получаем метаданные
        b = await msg.bot.download_file(f.file_path)  # скачиваем
        return (msg.audio.file_name or "audio.mp3"), b.read()  # возвращаем имя (если нет — дефолт) и байты
    if getattr(msg, "document", None):  # если прислали документ (PDF, DOCX и т.д.)
        f = await msg.bot.get_file(msg.document.file_id)  # получаем метаданные
        b = await msg.bot.download_file(f.file_path)  # скачиваем
        return (msg.document.file_name or "document"), b.read()  # возвращаем имя или "document" и байты
    if getattr(msg, "photo", None):  # если прислали фото
        f = await msg.bot.get_file(msg.photo[-1].file_id)  # берём самую большую версию изображения
        b = await msg.bot.download_file(f.file_path)  # скачиваем
        return "photo.jpg", b.read()  # возвращаем дефолтное имя и байты
    return "message.txt", (msg.text or "").encode("utf-8")  # если файла нет — упаковываем текст сообщения в txt

def _first_text(messages) -> Optional[str]:  # достаёт первый текстовый ответ ассистента из истории
    for m in messages.data:  # проходим по сообщениям
        if getattr(m, "role", None) == "assistant":  # ищем сообщения от ассистента
            for part in m.content:  # у сообщения может быть несколько частей (текст/картинки и т.п.)
                if part.type == "text":  # нас интересует текстовая часть
                    return part.text.value  # возвращаем текст
    return None  # если текста не нашли


# --- помощь: локи по thread_id ---

async def _acquire_thread_lock(thread_id: str):  # пытаемся захватить лок по треду
    if _redis:
        key = f"medbot:lock:thread:{thread_id}"  # ключ лока в Redis
        while True:
            ok = await _redis.set(key, "1", ex=120, nx=True)  # set NX + TTL
            if ok:
                return key  # получили лок
            await asyncio.sleep(0.2)  # ждём освобождения
    else:
        lock = _local_locks.setdefault(thread_id, asyncio.Lock())  # получаем/создаём лок в памяти
        await lock.acquire()
        return lock  # вернём сам лок-объект

# --- помощь: ожидание idle и ретраи messages.create ---

def _has_active_runs(runs_list) -> bool:  # проверка: есть ли активные run’ы в треде
    return any(getattr(r, "status", None) in _ACTIVE_RUN_STATUSES for r in runs_list.data)  # true, если есть активные

def _find_oldest_active(runs_list):  # найти «самый старый» активный run (на всякий)
    actives = [r for r in runs_list.data if getattr(r, "status", None) in _ACTIVE_RUN_STATUSES]
    return actives[-1] if actives else None  # список приходит отсортированным по дате у OpenAI SDK (новые сверху)

async def _wait_thread_idle(thread_id: str, timeout_s: int = 60, poll_s: float = 0.4):  # дожидаемся, пока тред будет «свободен»
    start = time.time()  # отметка времени
    while True:
        runs = client.beta.threads.runs.list(thread_id=thread_id, limit=10)  # смотрим активные run’ы
        if not _has_active_runs(runs):  # если активных нет — выходим
            return
        if time.time() - start > timeout_s:  # таймаут ожидания
            oldest = _find_oldest_active(runs)  # попробуем отменить «старый» run
            if oldest:
                try:
                    client.beta.threads.runs.cancel(thread_id=thread_id, run_id=oldest.id)  # мягкая отмена
                except Exception:
                    pass
            await asyncio.sleep(2)  # короткая пауза после cancel
            return  # выходим — пусть верхний уровень решает, что делать дальше
        await asyncio.sleep(poll_s)  # повторная проверка чуть позже

async def _messages_create_with_retry(thread_id: str, content, attachments=None, max_attempts: int = 3):  # безопасная отправка сообщения с ретраями
    for attempt in range(max_attempts):
        try:
            client.beta.threads.messages.create(  # добавляем сообщение пользователя в тред OpenAI
                thread_id=thread_id,
                role="user",
                content=content,
                attachments=attachments,
            )
            return  # успех
        except Exception as e:
            msg = str(getattr(e, "message", "")) or str(e)  # текст ошибки
            if "while a run" in msg and attempt < max_attempts - 1:  # классическая 400 «run is active»
                await asyncio.sleep(0.4 * (attempt + 1))  # бэкофф
                await _wait_thread_idle(thread_id, timeout_s=20)  # ещё раз убеждаемся, что тред idle
                continue
            raise  # если другая ошибка или исчерпали попытки — пробрасываем


# Проверим, какой механизм лока активен (для логов / самодиагностики)
try:
    bot_for_log = _log_bot or (Bot(LOG_BOT_TOKEN) if LOG_BOT_TOKEN else None)
    if bot_for_log and LOG_CHAT_ID:
        msg = "Redis lock backend: ENABLED" if _redis else "Redis lock backend: DISABLED (using in-memory)"
        asyncio.create_task(send_log(bot_for_log, msg))  # отправим сообщение в лог-чат асинхронно
except Exception:
    pass  # на случай, если лог-бот не инициализирован при старте


# --- основная задача ---
async def schedule_processing(msg: Message, delay_sec: Optional[int] = None) -> None:
    """Основной конвейер обработки сообщений пользователя."""
    try:
        delay = int(delay_sec if delay_sec is not None else DELAY_SEC)
        if delay > 0:
            await asyncio.sleep(delay)

        chat_id = msg.chat.id
        thread_id = get_or_create_thread(chat_id)
        await send_log(msg.bot, f"DEBUG ACK check={should_ack(chat_id, 3600)} chat_id={chat_id}")

        # 🔴 Отправляем ACK (раз в минуту)
        if should_ack(chat_id, cooldown_sec=60):
            typing_task = asyncio.create_task(_typing_for(msg.bot, chat_id, 20))  # 🔴 фоновый typing
            await asyncio.sleep(20)
            typing_task.cancel()
            ack_msg = await msg.answer(ACK_DELAYED)
            save_message(
                chat_id=chat_id,
                direction=1,
                text=ACK_DELAYED,
                content_type="system",
                message_id=getattr(ack_msg, "message_id", None),
            )

        base_text = msg.text or "Проанализируй вложение и ответь как медицинский консультант."
        content: List[Dict[str, Any]] = [{"type": "text", "text": base_text}]
        attachments = None

        # 🔴 Обработка вложений
        if any([getattr(msg, "voice", None),
                getattr(msg, "audio", None),
                getattr(msg, "document", None),
                getattr(msg, "photo", None)]):

            name, data = await _telegram_file_to_bytes(msg)
            fid = _upload_bytes(name, data)

            if _is_image(name):
                content.append({"type": "image_file", "image_file": {"file_id": fid}})
            elif _is_audio(name):
                try:
                    tr = client.audio.transcriptions.create(model="whisper-1", file=(name, io.BytesIO(data)))
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
                content[0]["text"] = f"{base_text}\n\n(Файл {name} загружен; если нужно, укажите правильный формат.)"

        # 🔴 Отправка в OpenAI
        lock_token = await _acquire_thread_lock(thread_id)
        try:
            await _wait_thread_idle(thread_id, timeout_s=60)
            await _messages_create_with_retry(thread_id, content, attachments, max_attempts=3)

            # 🔴 фоновый typing во время запроса ассистента
            typing_task = asyncio.create_task(_typing_for(msg.bot, chat_id, 60))

            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=ASSISTANT_ID,
                tool_choice="auto",
            )

            await send_log(msg.bot, f"🚀 Run {run.id} started for chat_id={chat_id}, thread={thread_id}")
            
            async def _release_thread_lock(lock_token):  # освобождение лока
                if _redis and isinstance(lock_token, str):
                    try:
                        await _redis.delete(lock_token)  # снимаем ключ лока в Redis
                    except Exception:
                        pass
                elif isinstance(lock_token, asyncio.Lock):
                    try:
                        lock_token.release()  # отпускаем лок в памяти процесса
                    except Exception:
                        pass

            
            # 🔴 Мониторинг статуса
            started = time.time()
            while True:
                run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
                if run.status in {"completed", "failed", "requires_action", "cancelled", "expired"}:
                    break
                await asyncio.sleep(2)
                if time.time() - started > 600:
                    try:
                        client.beta.threads.runs.cancel(thread_id=thread_id, run_id=run.id)
                    except Exception:
                        pass
                    break

            typing_task.cancel()  # 🔴 стоп typing при завершении
        finally:
            await _release_thread_lock(lock_token)

        # 🔴 Ответ пользователю
        if run.status == "completed":
            msgs = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=2)
            raw_txt = _first_text(msgs)
            if not raw_txt:
                raise RuntimeError("Empty response from assistant")

            clean = _sanitize_markdown(raw_txt)
            chunks = _split_for_delivery(clean) or [clean]

            # первая часть
            typing_task = asyncio.create_task(_typing_for(msg.bot, chat_id, 15))
            resp = await msg.answer(chunks[0])
            typing_task.cancel()
            save_message(chat_id, 1, chunks[0], "text", None, getattr(resp, "message_id", None))

            # остальные части
            for tail_part in chunks[1:]:
                typing_task = asyncio.create_task(_typing_for(msg.bot, chat_id, 10))
                respN = await msg.answer(tail_part)
                typing_task.cancel()
                save_message(chat_id, 1, tail_part, "text", None, getattr(respN, "message_id", None))
            return

        # если не completed
        await msg.answer("⚠️ Ошибка обработки. Попробуйте позже.")
        await send_log(msg.bot, f"run {run.id} finished with status={run.status}")
        _log_run_error(run)
    except Exception as e:
        await msg.answer("⚠️ Внутренняя ошибка. Пожалуйста, повторите позже.")
        await send_log(msg.bot, f"exception: {e}\n{traceback.format_exc()}")
