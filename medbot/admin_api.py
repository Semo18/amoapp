# admin_api.py
from __future__ import annotations  # аннотации без кавычек в 3.9+

# стандартная библиотека
from datetime import datetime, timedelta, timezone  # работа со временем

# типизация
from typing import Optional, List, Dict, Any  # типы для подсказок

# сторонние пакеты
from dateutil.parser import isoparse  # парсинг ISO/даты без времени
from fastapi import APIRouter, Query, HTTPException  # FastAPI-примитивы
from sqlalchemy import select, func, and_, desc, case  # конструкторы SQL

# локальные модули
from db import SessionLocal, User, Message  # сессия и ORM-модели
from repo import get_analytics_summary  # 🔴 единый расчёт сводки

# Роутер админ-API с общим префиксом.
router = APIRouter(prefix="/admin-api", tags=["admin"])  # роутер


# =================
# Вспомогательные
# =================

def _parse_dt(s: Optional[str],
              default: Optional[datetime]) -> datetime:
    """Разбираем ISO или YYYY-MM-DD. Без tz — считаем UTC."""
    if s:  # если строка дана — пробуем распарсить
        try:  # защищаемся от невалидного формата
            dt = isoparse(s)  # создаём datetime
            if dt.tzinfo is None:  # без tz — трактуем как UTC
                dt = dt.replace(tzinfo=timezone.utc)  # ставим UTC
            return dt.astimezone(timezone.utc)  # нормализуем к UTC
        except Exception:  # некорректные данные
            raise HTTPException(400, detail=f"Bad datetime format: {s}")  # 400
    if default is None:  # когда нет и строки, и дефолта
        raise HTTPException(400, detail="Missing date")  # 400
    return default  # отдаём дефолт


def _period_range(  # 🔴 единая точка преобразования периода в границы
    period: Optional[str],  # человекочитаемый период
    date_from: Optional[str],  # явная нижняя граница
    date_to: Optional[str],  # явная верхняя граница
) -> tuple[datetime, datetime]:
    """Возвращает полуинтервал [from, to) в UTC для фильтров."""
    now = datetime.now(timezone.utc)  # текущий момент UTC
    if date_from or date_to:  # если границы заданы явно
        start = _parse_dt(date_from, now - timedelta(days=1))  # нижняя
        end = _parse_dt(date_to, now)  # верхняя
        return start, end  # используем как есть

    p = (period or "day").lower()  # имя периода (по умолчанию day)
    today = datetime(now.year, now.month, now.day,
                     tzinfo=timezone.utc)  # полночь UTC

    if p == "day":  # сутки сегодня
        return today, today + timedelta(days=1)  # [00:00..+1d)
    if p == "week":  # последние 7 дней «от сейчас»
        return now - timedelta(days=7), now  # скользящее окно
    if p == "month":  # последние 30 суток «от сейчас»
        return now - timedelta(days=30), now  # скользящее окно

    if p in {"this_month", "current_month"}:  # 🔴 текущий месяц
        start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)  # 1-е
        return start, now  # до текущего момента

    if p in {"prev_month", "previous_month"}:  # 🔴 прошлый месяц
        first_this = datetime(now.year, now.month, 1,
                              tzinfo=timezone.utc)  # 1-е тек. мес
        end = first_this  # конец периода — начало текущего месяца
        prev_end = first_this - timedelta(days=1)  # последний день прошл.
        start = datetime(prev_end.year, prev_end.month, 1,
                         tzinfo=timezone.utc)  # 1-е прошл. мес
        return start, end  # полный прошлый месяц

    return now - timedelta(days=1), now  # дефолт «последние сутки»


# ===========
# Список чатов
# ===========

@router.get("/chats")  # отдаём чаты с их счётчиками за период
def list_chats(
    period: Optional[str] = Query(  # 🔴 предустановленные периоды
        None, description="day|week|month|this_month|prev_month"
    ),  # строковый пресет
    date_from: Optional[str] = Query(  # явная нижняя граница
        None, description="ISO or YYYY-MM-DD (inclusive)"
    ),  # строка-дата
    date_to: Optional[str] = Query(  # явная верхняя граница
        None, description="ISO or YYYY-MM-DD (exclusive)"
    ),  # строка-дата
    limit: int = Query(50, ge=1, le=200),  # размер страницы
    offset: int = Query(0, ge=0),  # смещение
) -> Dict[str, Any]:  # JSON с total+items
    """Чаты с количеством сообщений за выбранный период."""
    dt_from, dt_to = _period_range(period, date_from, date_to)  # 🔴 границы

    with SessionLocal() as s:  # одна сессия на запрос
        sub = (  # подзапрос: счётчик сообщений в диапазоне
            select(Message.chat_id, func.count().label("cnt"))  # chat_id+cnt
            .where(and_(Message.created_at >= dt_from,  # нижняя граница
                        Message.created_at < dt_to))  # верхняя граница
            .group_by(Message.chat_id)  # группируем по chat_id
            .subquery()  # подзапрос
        )
        q = (  # основной запрос по пользователям
            select(
                User.chat_id,  # идентификатор чата
                User.username,  # ник
                User.first_name,  # имя
                User.last_name,  # фамилия
                User.last_seen_at,  # последнее появление
                User.messages_total,  # всего сообщений за всё время
                func.coalesce(sub.c.cnt, 0).label("messages_in_period"),
            )  # счётчик за период
            .join(sub, sub.c.chat_id == User.chat_id, isouter=True)  # LEFT
            .order_by(desc("messages_in_period"),  # по активности
                      desc(User.last_seen_at))  # и по свежести
            .limit(limit)  # постранично
            .offset(offset)  # смещение
        )
        rows = s.execute(q).all()  # выполняем запрос

        q_total = select(func.count()).select_from(sub)  # всего активных
        total = s.execute(q_total).scalar() or 0  # число чатов

        items: List[Dict[str, Any]] = []  # собираем сериализацию
        for r in rows:  # каждая запись из результата
            items.append(
                {
                    "chat_id": r.chat_id,  # id
                    "username": r.username,  # ник
                    "first_name": r.first_name,  # имя
                    "last_name": r.last_name,  # фамилия
                    "last_seen_at": r.last_seen_at,  # последнее появление
                    "messages_total": r.messages_total,  # всего сообщений
                    "messages_in_period": int(r.messages_in_period or 0),
                }  # за период
            )
        return {"total": int(total), "items": items}  # ответ


# ==========================
# Сообщения конкретного чата
# ==========================

@router.get("/chats/{chat_id}/messages")  # эндпойнт: лента сообщений чата
def chat_messages(
    chat_id: int,  # идентификатор чата
    period: Optional[str] = Query(  # 🔴 пресет периода
        None, description="day|week|month|this_month|prev_month"
    ),
    date_from: Optional[str] = Query(None),  # явная нижняя граница
    date_to: Optional[str] = Query(None),  # явная верхняя граница
    limit: int = Query(50, ge=1, le=500),  # размер страницы
    offset: int = Query(0, ge=0),  # смещение
    q: Optional[str] = Query(  # 🔴 поиск по подстроке
        None, min_length=1, description="Поиск по тексту (ILIKE)"
    ),
    direction: Optional[int] = Query(  # 🔴 направление 0=user→bot, 1=bot→user
        None, ge=0, le=1, description="Фильтр по направлению (0/1)"
    ),
    content_type: Optional[str] = Query(  # 🔴 тип содержимого
        None, description="text|photo|audio|voice|document"
    ),
) -> Dict[str, Any]:  # JSON-ответ: { total, items }
    """
    Сообщения выбранного чата за указанный период.
    Поддерживает фильтрацию по направлению, типу контента и поиск по тексту.
    Логика: выбираем из messages только записи chat_id, попадающие в диапазон.
    """
    # 🔴 Определяем границы периода (по пресету или ручным датам)
    dt_from, dt_to = _period_range(period, date_from, date_to)

    # 🔴 Открываем сессию БД на время запроса
    with SessionLocal() as s:
        # 🔴 Формируем список условий выборки (WHERE)
        cond = [
            Message.chat_id == chat_id,  # обязательный фильтр по чату
            Message.created_at >= dt_from,  # нижняя граница
            Message.created_at < dt_to,  # верхняя граница
        ]

        # 🔴 При необходимости добавляем фильтр по направлению
        if direction in (0, 1):
            cond.append(Message.direction == direction)

        # 🔴 Фильтр по типу контента (если задан)
        if content_type:
            cond.append(Message.content_type == content_type)

        # 🔴 Поиск по подстроке текста (ILIKE)
        if q:
            ilike = f"%{q.strip()}%"
            cond.append(Message.text.ilike(ilike))

        # 🔴 Собираем основной запрос выборки сообщений
        q_stmt = (
            select(
                Message.id,  # PK
                Message.direction,  # 0/1
                Message.text,  # текст сообщения
                Message.content_type,  # тип контента
                Message.attachment_name,  # имя вложения
                Message.created_at,  # дата и время
            )
            .where(and_(*cond))  # применяем все условия
            .order_by(desc(Message.created_at))  # сортировка: новые сверху
            .limit(limit)  # ограничение по странице
            .offset(offset)  # смещение для пагинации
        )

        # 🔴 Выполняем основной запрос
        rows = s.execute(q_stmt).all()

        # 🔴 Отдельный запрос для общего количества (без пагинации)
        q_total = select(func.count()).where(and_(*cond))
        total = s.execute(q_total).scalar() or 0  # безопасное значение

        # 🔴 Преобразуем ORM-результаты в список словарей (JSON-friendly)
        items: List[Dict[str, Any]] = []
        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "direction": r.direction,
                    "text": r.text,
                    "content_type": r.content_type,
                    "attachment_name": r.attachment_name,
                    "created_at": (
                        r.created_at.isoformat()
                        if hasattr(r.created_at, "isoformat")
                        else r.created_at
                    ),  # 🔴 в ISO 8601
                }
            )

        # 🔴 Возвращаем стандартную структуру для фронта
        return {"total": int(total), "items": items}



# =========
# Аналитика
# =========

@router.get("/analytics/summary")  # сводка за период
def analytics_summary_api(
    period: Optional[str] = Query(  # 🔴 предустановленные периоды
        None, description="day|week|month|this_month|prev_month"
    ),  # строковый пресет
    date_from: Optional[str] = Query(None),  # явная нижняя граница
    date_to: Optional[str] = Query(None),  # явная верхняя граница
) -> Dict[str, Any]:  # JSON со счётчиками
    """Короткая сводка: всего/входящих/исходящих/пользователи."""
    dt_from, dt_to = _period_range(period, date_from, date_to)  # 🔴 границы
    return get_analytics_summary(dt_from, dt_to)  # 🔴 единый расчёт


@router.get("/analytics/users")  # активность по пользователям
def analytics_users(
    period: Optional[str] = Query(  # 🔴 предустановленные периоды
        None, description="day|week|month|this_month|prev_month"
    ),  # строковый пресет
    date_from: Optional[str] = Query(None),  # явная нижняя граница
    date_to: Optional[str] = Query(None),  # явная верхняя граница
    limit: int = Query(50, ge=1, le=500),  # размер страницы
    offset: int = Query(0, ge=0),  # смещение
) -> Dict[str, Any]:  # JSON с total+items
    """Разбивка: входящие/исходящие по chat_id. Сортировка по сумме."""
    dt_from, dt_to = _period_range(period, date_from, date_to)  # 🔴 границы

    with SessionLocal() as s:  # одна сессия на запрос
        agg = (  # агрегируем I/O по каждому chat_id
            select(
                Message.chat_id,  # ключ агрегации
                func.sum(  # считаем входящие
                    case((Message.direction == 0, 1), else_=0)
                ).label("messages_in"),
                func.sum(  # считаем исходящие
                    case((Message.direction == 1, 1), else_=0)
                ).label("messages_out"),
                func.min(Message.created_at).label("first_in_period"),
            )
            .where(and_(Message.created_at >= dt_from,  # границы
                        Message.created_at < dt_to))  # границы
            .group_by(Message.chat_id)  # группируем по чату
            .subquery()  # подзапрос
        )

        q = (  # присоединяем к таблице пользователей
            select(
                User.chat_id,  # id
                User.username,  # ник
                User.first_name,  # имя
                User.last_name,  # фамилия
                agg.c.messages_in,  # входящие
                agg.c.messages_out,  # исходящие
                agg.c.first_in_period,  # первая активность
            )
            .join(agg, agg.c.chat_id == User.chat_id)  # INNER: только акт.
            .order_by(desc(agg.c.messages_in + agg.c.messages_out))  # сорт
            .limit(limit)  # страница
            .offset(offset)  # смещение
        )
        rows = s.execute(q).all()  # выполняем

        q_total = select(func.count()).select_from(agg)  # всего записей
        total = s.execute(q_total).scalar() or 0  # число пользователей

        items: List[Dict[str, Any]] = []  # сериализация
        for r in rows:  # каждая запись
            items.append(
                {
                    "chat_id": r.chat_id,  # id
                    "username": r.username,  # ник
                    "first_name": r.first_name,  # имя
                    "last_name": r.last_name,  # фамилия
                    "messages_in": int(r.messages_in or 0),  # вход.
                    "messages_out": int(r.messages_out or 0),  # исход.
                    "first_seen_in_period": r.first_in_period,  # первая
                }
            )
        return {"total": int(total), "items": items}  # ответ
