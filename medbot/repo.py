# repo.py
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from db import SessionLocal, User, Message

def upsert_user_from_msg(msg) -> None:
    """Создаёт/обновляет пользователя по входящему сообщению."""
    with SessionLocal() as s:
        u = s.execute(select(User).where(User.chat_id == msg.chat.id)).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if not u:
            u = User(
                chat_id=msg.chat.id,
                username=getattr(msg.from_user, "username", None),
                first_name=getattr(msg.from_user, "first_name", None),
                last_name=getattr(msg.from_user, "last_name", None),
                language_code=getattr(msg.from_user, "language_code", None),
                first_seen_at=now,
                last_seen_at=now,
                messages_total=1,
            )
            s.add(u)
        else:
            u.username = getattr(msg.from_user, "username", u.username)
            u.first_name = getattr(msg.from_user, "first_name", u.first_name)
            u.last_name = getattr(msg.from_user, "last_name", u.last_name)
            u.language_code = getattr(msg.from_user, "language_code", u.language_code)
            u.last_seen_at = now
            u.messages_total = (u.messages_total or 0) + 1
        s.commit()

def save_message(
    chat_id: int,
    direction: int,  # 0=in, 1=out
    text: Optional[str] = None,
    content_type: str = "text",
    attachment_name: Optional[str] = None,
    message_id: Optional[int] = None,
) -> None:
    """Сохраняет сообщение любой стороны."""
    with SessionLocal() as s:
        s.add(Message(
            chat_id=chat_id,
            direction=direction,
            message_id=message_id,
            text=text,
            content_type=content_type,
            attachment_name=attachment_name,
        ))
        s.commit()
