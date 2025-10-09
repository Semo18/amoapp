# bot.py
import os  # работа с переменными окружения (читаем настройки из .env или системы)
import asyncio  # библиотека для работы с асинхронными задачами (параллельные действия)
from aiogram import Router, F, Bot  # Router — маршрутизация сообщений, F — фильтры, Bot — объект бота
from aiogram.filters import CommandStart, Command  # фильтры для команд /start и других
from aiogram.types import Message  # тип для входящих сообщений от пользователей
from aiogram.enums import ChatAction  # типы действий в чате (например, "печатает...")
from storage import should_ack  # функция, проверяющая нужно ли отправить авто-уведомление
from repo import upsert_user_from_msg, save_message  # функции записи в БД (пользователь/сообщение)

from texts import WELCOME, DISCLAIMER, ACK_DELAYED  # заранее заготовленные тексты для приветствия, дисклеймера и авто-ответа
from openai_client import schedule_processing, ensure_thread_choice  # функции для интеграции с OpenAI

# На время разработки 60 сек; можно переопределить в .env -> REPLY_DELAY_SEC
DELAY_SEC = int(os.getenv("REPLY_DELAY_SEC", "60"))  # задержка ответа (по умолчанию 60 сек, можно задать через .env)

router = Router()  # создаём маршрутизатор сообщений

def setup_handlers(dp):  # подключаем все обработчики в диспетчер
    dp.include_router(router)  # регистрируем router внутри диспетчера

# --- обработчики команд ---

@router.message(CommandStart())  # если пользователь написал /start
async def cmd_start(msg: Message):
    await msg.answer(WELCOME)  # отправляем приветственный текст
    await asyncio.sleep(0.3)  # ждём 0.3 сек для "человеческой" паузы
    await msg.answer(DISCLAIMER, disable_web_page_preview=True)  # отправляем дисклеймер (с отключённым предпросмотром ссылок)

@router.message(Command("new"))  # если пользователь написал /new
async def cmd_new(msg: Message):
    text = (
        "Хотите продолжить текущий медицинский диалог или начать новый?\n"
        "• Напишите: «продолжить» — чтобы общаться в текущем треде\n"
        "• Напишите: «новый» — чтобы создать новый тред"
    )
    await msg.answer(text)  # задаём пользователю выбор

@router.message(F.text.lower().in_({"продолжить", "новый"}))  # если пришёл текст "продолжить" или "новый"
async def on_thread_choice(msg: Message):
    created = await ensure_thread_choice(msg.chat.id, msg.text.lower())  # проверяем выбор пользователя и создаём новый тред, если нужно
    await msg.answer("Готово. " + ("Создан новый тред." if created else "Продолжаем текущий тред."))  # отправляем подтверждение

# --- утилита для "трёх точек" ---
async def _typing_later(bot: Bot, chat_id: int, start_in: int, duration: int = 60):
    """Показываем ChatAction.TYPING начиная через start_in сек и ~duration сек поддерживаем индикатор."""
    await asyncio.sleep(max(0, start_in))  # ждём, когда нужно начать показывать "печатает..."
    loop = asyncio.get_event_loop()  # получаем текущий цикл событий
    until = loop.time() + max(1, duration)  # до какого времени показывать индикатор
    while loop.time() < until:  # пока не истекло время
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)  # отправляем действие "печатает..." в чат
        except Exception:
            pass  # если ошибка — игнорируем (например, пользователь закрыл чат)
        await asyncio.sleep(4)  # Telegram держит индикатор ~5 сек, поэтому обновляем каждые 4 сек

# --- определение типа входящего сообщения (для записи в БД) ---
def _infer_msg_type(msg: Message) -> str:
    if getattr(msg, "voice", None): return "voice"  # голосовое
    if getattr(msg, "audio", None): return "audio"  # аудиофайл
    if getattr(msg, "photo", None): return "photo"  # фото
    if getattr(msg, "document", None): return "document"  # документ
    return "text"  # обычный текст

# --- обработчик любых сообщений ---
@router.message()  # срабатывает на любое сообщение пользователя
async def any_message(msg: Message, bot: Bot):
    chat_id = msg.chat.id  # ID чата, откуда пришло сообщение

    # --- фиксируем пользователя и входящее сообщение в БД ---
    upsert_user_from_msg(msg)  # создаём/обновляем пользователя и счётчик сообщений
    incoming_type = _infer_msg_type(msg)  # определяем вид сообщения
    save_message(  # сохраняем само входящее сообщение
        chat_id=chat_id,
        direction=0,  # 0 = входящее (от пользователя)
        text=msg.text if incoming_type == "text" else None,  # текст сохраняем только если он есть
        content_type=incoming_type,  # тип контента
        attachment_name=(
            getattr(msg.audio, "file_name", None)  # имя аудиофайла
            or getattr(msg.document, "file_name", None)  # имя документа
            or ("photo" if getattr(msg, "photo", None) else None)  # помета фото
            or ("voice" if getattr(msg, "voice", None) else None)  # помета голосового
        ),
        message_id=getattr(msg, "message_id", None),  # телеграмный message_id (если нужен)
    )

    # 1) "три точки" за минуту до ответа (или сразу, если задержка < 60)
    start_in = max(0, DELAY_SEC - 60)  # когда начать показывать "печатает..."
    typing_duration = min(60, DELAY_SEC)  # сколько секунд показывать
    asyncio.create_task(_typing_later(bot, chat_id, start_in, typing_duration))  # запускаем задачу показа индикатора печати

    # 2) запускаем обработку В ФОНЕ (не блокируем хендлер)
    asyncio.create_task(schedule_processing(msg, delay_sec=DELAY_SEC))  # отправляем сообщение на обработку OpenAI с задержкой
    #Тест на деплой 
