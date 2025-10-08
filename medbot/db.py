# db.py
import os
from sqlalchemy import create_engine, Column, BigInteger, Integer, Text, Boolean, SmallInteger, DateTime, String, func
from sqlalchemy.orm import sessionmaker, declarative_base

DB_URL = os.getenv("DB_URL", "postgresql+psycopg://vl:vlpass@178.62.255.113:5433/vl_admin")  # 🔴 новый драйвер


engine = create_engine(DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(Text)
    first_name = Column(Text)
    last_name = Column(Text)
    language_code = Column(String(8))
    is_blocked = Column(Boolean, nullable=False, default=False)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    messages_total = Column(Integer, nullable=False, default=0)


class Message(Base):
    __tablename__ = "messages"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=False)
    direction = Column(SmallInteger, nullable=False)  # 0=in, 1=out
    message_id = Column(BigInteger)
    text = Column(Text)
    content_type = Column(String(32), nullable=False, default="text")
    attachment_name = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
