# storage.py
import os  # os — окружение
import time  # time — метки времени
import redis  # библиотека Redis
from typing import Optional, Dict
from constants import (  # 🔴 централизованные ключи и префиксы
    REDIS_THREAD_KEY,  # 🔴
    REDIS_LAST_SEEN_KEY,  # 🔴
    REDIS_ACK_ONCE_PREFIX,  # 🔴
    REDIS_LAST_ACK_PREFIX,  # 🔴
    REDIS_LEAD_ID_KEY,  # 🔴
)

# берём адрес Redis из переменных окружения; по умолчанию — локальный
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# создаём подключение; строки вместо байтов упрощают работу # 🔴
r = redis.from_url(
    REDIS_URL, decode_responses=True
)  # 🔴

# ключи теперь берём из общего модуля констант
# KEY = "medbot:tchat:thread"  # 🔴 перенесено в constants

# локальный словарь времени последнего сообщения
_last_seen: Dict[int, int] = {}


def get_thread_id(chat_id: int):
    # получаем thread_id для чата из Redis
    return r.hget(REDIS_THREAD_KEY, chat_id)  # 🔴


def set_thread_id(chat_id: int, thread_id: str):
    # сохраняем thread_id и время последней активности
    r.hset(REDIS_THREAD_KEY, chat_id, thread_id)  # 🔴
    r.hset(REDIS_LAST_SEEN_KEY, chat_id, int(time.time()))  # 🔴


def drop_thread_id(chat_id: int):
    # удаляем связку и «последнюю активность»
    r.hdel(REDIS_THREAD_KEY, chat_id)  # 🔴
    r.hdel(REDIS_LAST_SEEN_KEY, chat_id)  # 🔴


def ack_once(chat_id: int, ttl_seconds: int = 24 * 3600) -> bool:
    """
    Проверяет, отправляли ли уже авто-квиток.
    Если ещё не отправляли — возвращает True и помечает, что отправлен.
    TTL — время хранения пометки (по умолчанию 24 часа).
    """
    key = f"{REDIS_ACK_ONCE_PREFIX}{chat_id}"  # 🔴
    # SET NX EX: записать, если не было; с TTL
    return bool(r.set(key, "1", nx=True, ex=ttl_seconds))  # 🔴


def should_ack(chat_id: int, cooldown_sec: int = 3600) -> bool:
    """
    Решает, нужно ли снова отправить авто-квиток
    (например, "Ваш запрос принят"). Возвращает True, если прошло
    больше заданного времени (по умолчанию 1 час).
    """
    key = f"{REDIS_LAST_ACK_PREFIX}{chat_id}"  # 🔴
    now = int(time.time())  # текущее время
    last = r.get(key)  # время предыдущего авто-квитка
    if last is None or (now - int(last)) > cooldown_sec:
        r.set(key, now)  # обновляем время последнего авто-квитка
        return True  # можно отправить новый авто-квиток
    return False  # рано отправлять, ждём ещё


def get_lead_id(chat_id: int) -> Optional[str]:
    # получаем связанную сделку amoCRM для чата
    return r.hget(REDIS_LEAD_ID_KEY, chat_id)  # 🔴


def set_lead_id(chat_id: int, lead_id: str):
    # сохраняем связку чат → сделка
    r.hset(REDIS_LEAD_ID_KEY, chat_id, lead_id)  # 🔴
