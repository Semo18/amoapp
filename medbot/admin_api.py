# admin_api.py
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
from dateutil.parser import isoparse
from sqlalchemy import select, func, and_, desc, case
from db import SessionLocal, User, Message

router = APIRouter(prefix="/admin-api", tags=["admin"])

def _parse_dt(s: Optional[str], default: Optional[datetime]) -> datetime:
    if s:
        # допускаем YYYY-MM-DD или ISO с временем
        try:
            dt = isoparse(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            raise HTTPException(400, detail=f"Bad datetime format: {s}")
    if default is None:
        raise HTTPException(400, detail="Missing date")
    return default

@router.get("/chats")
def list_chats(
    date_from: Optional[str] = Query(None, description="ISO или YYYY-MM-DD"),
    date_to: Optional[str]   = Query(None, description="ISO или YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    # по умолчанию последние 24 часа
    now = datetime.now(timezone.utc)
    dt_from = _parse_dt(date_from, now - timedelta(days=1))
    dt_to = _parse_dt(date_to, now)

    with SessionLocal() as s:
        # пользователи, у кого были сообщения в периоде
        sub = (
            select(Message.chat_id, func.count().label("cnt"))
            .where(and_(Message.created_at >= dt_from, Message.created_at < dt_to))
            .group_by(Message.chat_id)
            .subquery()
        )
        q = (
            select(
                User.chat_id,
                User.username,
                User.first_name,
                User.last_name,
                User.last_seen_at,
                User.messages_total,
                func.coalesce(sub.c.cnt, 0).label("messages_in_period"),
            )
            .join(sub, sub.c.chat_id == User.chat_id, isouter=True)
            .order_by(desc("messages_in_period"), desc(User.last_seen_at))
            .limit(limit)
            .offset(offset)
        )
        rows = s.execute(q).all()

        # общее кол-во уникальных чатов в периоде (для пагинации)
        q_total = select(func.count()).select_from(sub)
        total = s.execute(q_total).scalar() or 0

        items = []
        for r in rows:
            items.append({
                "chat_id": r.chat_id,
                "username": r.username,
                "first_name": r.first_name,
                "last_name": r.last_name,
                "last_seen_at": r.last_seen_at,
                "messages_total": r.messages_total,
                "messages_in_period": int(r.messages_in_period or 0),
            })
        return {"total": int(total), "items": items}

@router.get("/chats/{chat_id}/messages")
def chat_messages(
    chat_id: int,
    date_from: Optional[str] = Query(None),
    date_to: Optional[str]   = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    dt_from = _parse_dt(date_from, now - timedelta(days=1))
    dt_to = _parse_dt(date_to, now)

    with SessionLocal() as s:
        q = (
            select(
                Message.id,
                Message.direction,
                Message.text,
                Message.content_type,
                Message.attachment_name,
                Message.created_at,
            )
            .where(and_(
                Message.chat_id == chat_id,
                Message.created_at >= dt_from,
                Message.created_at < dt_to,
            ))
            .order_by(desc(Message.created_at))
            .limit(limit)
            .offset(offset)
        )
        rows = s.execute(q).all()

        # total
        q_total = select(func.count()).where(and_(
            Message.chat_id == chat_id,
            Message.created_at >= dt_from,
            Message.created_at < dt_to,
        ))
        total = s.execute(q_total).scalar() or 0

        items = []
        for r in rows:
            items.append({
                "id": r.id,
                "direction": r.direction,
                "text": r.text,
                "content_type": r.content_type,
                "attachment_name": r.attachment_name,
                "created_at": r.created_at,
            })
        return {"total": int(total), "items": items}

@router.get("/analytics/summary")
def analytics_summary(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str]   = Query(None),
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    dt_from = _parse_dt(date_from, now - timedelta(days=1))
    dt_to = _parse_dt(date_to, now)

    with SessionLocal() as s:
        # всего уникальных пользователей, писавших в периоде
        q_users = select(func.count(func.distinct(Message.chat_id))).where(
            and_(Message.created_at >= dt_from, Message.created_at < dt_to)
        )
        users_total = s.execute(q_users).scalar() or 0

        # всего сообщений / входящих / исходящих
        q_all = select(func.count()).where(and_(Message.created_at >= dt_from, Message.created_at < dt_to))
        all_total = s.execute(q_all).scalar() or 0

        q_in = select(func.count()).where(and_(Message.created_at >= dt_from, Message.created_at < dt_to, Message.direction == 0))
        q_out = select(func.count()).where(and_(Message.created_at >= dt_from, Message.created_at < dt_to, Message.direction == 1))
        messages_in = s.execute(q_in).scalar() or 0
        messages_out = s.execute(q_out).scalar() or 0

        return {
            "users_total": int(users_total),
            "messages_total": int(all_total),
            "messages_in": int(messages_in),
            "messages_out": int(messages_out),
        }

@router.get("/analytics/users")
def analytics_users(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str]   = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    dt_from = _parse_dt(date_from, now - timedelta(days=1))
    dt_to = _parse_dt(date_to, now)

    with SessionLocal() as s:
        # агрегируем вход/выход по пользователям
        agg = (
            select(
                Message.chat_id,
                func.sum(case((Message.direction == 0, 1), else_=0)).label("messages_in"),
                func.sum(case((Message.direction == 1, 1), else_=0)).label("messages_out"),
                func.min(Message.created_at).label("first_in_period"),
            )
            .where(and_(Message.created_at >= dt_from, Message.created_at < dt_to))
            .group_by(Message.chat_id)
            .subquery()
        )

        q = (
            select(
                User.chat_id,
                User.username,
                User.first_name,
                User.last_name,
                agg.c.messages_in,
                agg.c.messages_out,
                agg.c.first_in_period,
            )
            .join(agg, agg.c.chat_id == User.chat_id)
            .order_by(desc(agg.c.messages_in + agg.c.messages_out))
            .limit(limit)
            .offset(offset)
        )
        rows = s.execute(q).all()

        q_total = select(func.count()).select_from(agg)
        total = s.execute(q_total).scalar() or 0

        items = []
        for r in rows:
            items.append({
                "chat_id": r.chat_id,
                "username": r.username,
                "first_name": r.first_name,
                "last_name": r.last_name,
                "messages_in": int(r.messages_in or 0),
                "messages_out": int(r.messages_out or 0),
                "first_seen_in_period": r.first_in_period,
            })
        return {"total": int(total), "items": items}
