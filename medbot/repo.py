# repo.py
from __future__ import annotations  # аннотации без кавычек в 3.9+

# стандартная библиотека
from datetime import datetime, timedelta, timezone  # работа со временем
from typing import Optional, List, Dict, Any  # типы для подсказок

# сторонние пакеты
from sqlalchemy import select, and_, desc  # конструкторы запросов
from sqlalchemy.sql import func, case  # агрегаты и условные выражения

# локальные модули проекта
from db import SessionLocal, User, Message  # сессия и ORM-модели
import redis


# ==========================
# Помощники по пользователю
# ==========================

def upsert_user_from_msg(msg) -> None:
    """Создаём/обновляем пользователя по входящему сообщению.
    Стратегия: одна транзакция; если записи нет — вставляем, иначе
    мягко обновляем основные поля и счётчик сообщений.
    """
    with SessionLocal() as s:  # открываем короткую сессию на оперцию
        q = select(User).where(User.chat_id == msg.chat.id)  # ищем по chat
        u: Optional[User] = s.execute(q).scalar_one_or_none()  # берём одну

        now = datetime.now(timezone.utc)  # фиксируем «момент измерения»

        if not u:  # нет записи — создаём с начальными значениями
            u = User(
                chat_id=msg.chat.id,  # внешний идентификатор TG
                username=getattr(msg.from_user, "username", None),  # ник
                first_name=getattr(msg.from_user, "first_name", None),  # имя
                last_name=getattr(msg.from_user, "last_name", None),  # фам.
                language_code=getattr(  # язык интерфейса TG
                    msg.from_user, "language_code", None
                ),
                first_seen_at=now,  # впервые увидели
                last_seen_at=now,  # последнее «видели»
                messages_total=1,  # первый инкремент
            )
            s.add(u)  # ставим на инсерт
        else:  # запись есть — обновляем минимально необходимое
            u.username = getattr(msg.from_user, "username", u.username)
            u.first_name = getattr(msg.from_user, "first_name", u.first_name)
            u.last_name = getattr(msg.from_user, "last_name", u.last_name)
            u.language_code = getattr(
                msg.from_user, "language_code", u.language_code
            )
            u.last_seen_at = now  # двигаем «последний визит»
            u.messages_total = (u.messages_total or 0) + 1  # счётчик ↑

        s.commit()  # фиксируем транзакцию


def save_message(
    chat_id: int,
    direction: int,  # 0 = пользователь→бот, 1 = бот→пользователь
    text: Optional[str] = None,
    content_type: str = "text",
    attachment_name: Optional[str] = None,
    message_id: Optional[int] = None,
) -> None:
    """Сохраняем отдельное сообщение (любой стороны)."""
    with SessionLocal() as s:  # короткая транзакция на запись
        m = Message(
            chat_id=chat_id,
            direction=direction,
            message_id=message_id,
            text=text,
            content_type=content_type,
            attachment_name=attachment_name,
        )
        s.add(m)  # ставим на инсерт
        s.commit()  # фиксируем


# ==========================
# Сообщения: выдача для UI
# ==========================

def fetch_messages(
    chat_id: int,
    limit: int = 20,
    before_id: Optional[int] = None,  # курсор «старее чем id»
    after_id: Optional[int] = None,   # курсор «новее чем id»
    q: Optional[str] = None,          # поиск по подстроке (ILIKE)
    direction: Optional[int] = None,  # 0/1
    content_type: Optional[str] = None,
    order: str = "desc",              # 'desc' | 'asc'
) -> Dict[str, Any]:
    """Возвращает порцию сообщений с фильтрами и курсорами.
    Стратегия: строим один SELECT с WHERE по заданным фильтрам, сортируем
    по id (стабильно коррелирует со временем), возвращаем курсоры:
    - next_before: подставлять в before_id для «прокрутки вниз»,
    - next_after:  подставлять в after_id для «обновления вверх».
    """
    with SessionLocal() as s:  # одна сессия на выдачу страницы
        cond = [Message.chat_id == chat_id]  # базовый фильтр по чату

        if direction in (0, 1):  # фильтр направления (если задан)
            cond.append(Message.direction == direction)

        if content_type:  # фильтр по типу
            cond.append(Message.content_type == content_type)

        if q:  # поиск по тексту
            ilike = f"%{q.strip()}%"
            cond.append(Message.text.ilike(ilike))

        if before_id is not None:  # курсор «старше»
            cond.append(Message.id < before_id)

        if after_id is not None:  # курсор «новее»
            cond.append(Message.id > after_id)

        stmt = select(Message).where(and_(*cond))  # собираем WHERE

        if order.lower() == "asc":  # направление сортировки
            stmt = stmt.order_by(Message.id.asc())
        else:
            stmt = stmt.order_by(desc(Message.id))

        stmt = stmt.limit(max(1, min(200, limit)))  # безопасный лимит

        rows: List[Message] = list(s.execute(stmt).scalars())  # выполняем

        # сериализуем ORM-строки в плоские словари для фронтенда
        items: List[Dict[str, Any]] = []
        for m in rows:
            items.append(
                {
                    "id": m.id,
                    "chat_id": m.chat_id,
                    "direction": m.direction,
                    "text": m.text,
                    "content_type": m.content_type,
                    "attachment_name": m.attachment_name,
                    "message_id": m.message_id,
                    "created_at": (
                        m.created_at.isoformat() if m.created_at else None
                    ),
                }
            )

        # курсоры для следующей порции
        if items:
            min_id = items[0]["id"] if order == "asc" else items[-1]["id"]
            max_id = items[-1]["id"] if order == "asc" else items[0]["id"]
        else:
            min_id = None
            max_id = None

        return {
            "items": items,
            "next_before": min_id,  # прокрутка вниз (старее)
            "next_after": max_id,   # обновление вверх (новее)
        }


# ==========================
# Админ: списки и аналитика
# ==========================

def list_chats_with_counters(
    period: str = "day",
) -> Dict[str, Any]:
    """Список чатов с числом сообщений «за период» и «всего».
    Стратегия: считаем 2 числа на пользователя отдельными COUNT-ами —
    просто, надёжно и достаточно быстро на нашем объёме.
    """
    now = datetime.now(timezone.utc)  # фиксируем «сейчас» в UTC
    span = {"day": 1, "week": 7, "month": 30}.get(period, 1)  # окно в днях
    since = now - timedelta(days=span)  # нижняя граница

    with SessionLocal() as s:  # одна сессия на всю выборку
        users: List[User] = list(s.execute(select(User)).scalars())

        result: List[Dict[str, Any]] = []  # сюда собираем карточки

        for u in users:  # последовательная агрегация по каждому юзеру
            total_q = select(func.count(Message.id)).where(
                Message.chat_id == u.chat_id
            )
            total = s.execute(total_q).scalar_one()

            period_q = select(func.count(Message.id)).where(
                and_(Message.chat_id == u.chat_id, Message.created_at >= since)
            )
            period_cnt = s.execute(period_q).scalar_one()

            result.append(
                {
                    "chat_id": u.chat_id,
                    "username": u.username,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                    "last_seen_at": (
                        u.last_seen_at.isoformat() if u.last_seen_at else None
                    ),
                    "messages_total": int(total),
                    "messages_in_period": int(period_cnt),
                }
            )

        result.sort(  # сверху — самые «живые» за период
            key=lambda x: x["messages_in_period"], reverse=True
        )

        return {"total": len(result), "items": result}


def analytics_summary(period: str = "day") -> Dict[str, int]:
    """Короткая сводка по периодам day|week|month (наследие UI).
    Стратегия: считаем агрегаты одним запросом на каждый показатель.
    """
    now = datetime.now(timezone.utc)  # метка времени сейчас
    span = {"day": 1, "week": 7, "month": 30}.get(period, 1)  # окно
    since = now - timedelta(days=span)  # нижняя граница

    with SessionLocal() as s:  # одна сессия на все COUNT-ы
        users_total = s.execute(
            select(func.count(User.chat_id))
        ).scalar_one()

        msgs_total = s.execute(
            select(func.count(Message.id)).where(Message.created_at >= since)
        ).scalar_one()

        msgs_in = s.execute(
            select(func.count(Message.id)).where(
                and_(Message.created_at >= since, Message.direction == 0)
            )
        ).scalar_one()

        msgs_out = s.execute(
            select(func.count(Message.id)).where(
                and_(Message.created_at >= since, Message.direction == 1)
            )
        ).scalar_one()

        return {
            "users_total": int(users_total),
            "messages_total": int(msgs_total),
            "messages_in": int(msgs_in),
            "messages_out": int(msgs_out),
        }


# ==========================
# Агрегаты за произвольный период
# ==========================

def get_analytics_summary(  # новая универсальная функция
    date_from: Optional[datetime],  # включительно
    date_to: Optional[datetime],    # исключительно
) -> Dict[str, int]:
    """Сводка по сообщениям за [date_from, date_to).
    Стратегия: один SELECT с агрегатами и CASE, чтобы не гонять
    несколько отдельных COUNT-ов. Границы None — без ограничения.
    """
    with SessionLocal() as s:  # одна сессия на агрегацию
        stmt = select(  # собираем единый агрегирующий запрос
            func.count(Message.id).label("messages_total"),
            func.sum(
                case((Message.direction == 0, 1), else_=0)
            ).label("messages_in"),
            func.sum(
                case((Message.direction == 1, 1), else_=0)
            ).label("messages_out"),
            func.count(func.distinct(Message.chat_id)).label(
                "users_total"
            ),
        )
        if date_from is not None:  # нижняя граница, если задана
            stmt = stmt.where(Message.created_at >= date_from)
        if date_to is not None:  # верхняя граница, если задана
            stmt = stmt.where(Message.created_at < date_to)

        row = s.execute(stmt).one()  # выполняем и читаем строку

        return {  # нормализуем None → 0 и приводим к int
            "users_total": int(row.users_total or 0),
            "messages_total": int(row.messages_total or 0),
            "messages_in": int(row.messages_in or 0),
            "messages_out": int(row.messages_out or 0),
        }

# ==========================
# Загрузка файлов в amoCRM
# ==========================

import aiohttp
import logging
import os


AMO_API_URL = os.getenv("AMO_API_URL", "")
AMO_ACCESS_TOKEN = os.getenv("AMO_ACCESS_TOKEN", "")


async def upload_file_to_amo(file_name: str, file_bytes: bytes) -> Optional[str]:
    """
    Загружает файл в amoCRM и возвращает UUID загруженного файла.
    Используется при создании сделок из Telegram.
    """
    try:
        # создаём HTTP-сессию
        async with aiohttp.ClientSession() as session:
            # готовим форму multipart/form-data
            form = aiohttp.FormData()
            form.add_field(
                "file",
                file_bytes,
                filename=file_name,
                content_type="application/octet-stream",
            )

            # отправляем POST-запрос в amoCRM API
            async with session.post(
                f"{AMO_API_URL}/api/v4/files",
                headers={"Authorization": f"Bearer {AMO_ACCESS_TOKEN}"},
                data=form,
            ) as resp:
                if resp.status == 200:
                    # читаем JSON и возвращаем UUID файла
                    data = await resp.json()
                    uuid = data.get("uuid")
                    logging.info(f"✅ File uploaded to amoCRM: {file_name}")
                    return uuid
                else:
                    # ошибка на стороне amoCRM — логируем
                    text = await resp.text()
                    logging.warning(
                        f"⚠️ Failed to upload file [{resp.status}]: {text}"
                    )
                    return None
    except Exception as e:
        logging.warning(f"⚠️ upload_file_to_amo exception: {e}")
        return None

# ==========================
# Связка chat_id ↔ lead_id (Redis)
# ==========================

# 🔴 эти функции нужны для умной привязки сделок к пользователя

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")  # адрес Redis
r = redis.from_url(REDIS_URL, decode_responses=True)


def set_lead_id(chat_id: int, lead_id: str) -> None:
    """🔴 Привязывает chat_id → lead_id (чтобы новые сообщения добавлялись в ту же сделку)."""
    r.hset("medbot:lead", chat_id, lead_id)


def get_lead_id(chat_id: int) -> Optional[str]:
    """🔴 Возвращает связанный lead_id, если есть."""
    return r.hget("medbot:lead", chat_id)
