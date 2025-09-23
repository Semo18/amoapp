import os, time
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(REDIS_URL, decode_responses=True)
KEY = "medbot:tchat:thread"


def get_thread_id(chat_id: int):
    return r.hget(KEY, chat_id)


def set_thread_id(chat_id: int, thread_id: str):
    r.hset(KEY, chat_id, thread_id)
    # для авточистки неактивных >90д — храним ts последней активности
    r.hset("medbot:last_seen", chat_id, int(time.time()))


def drop_thread_id(chat_id: int):
    r.hdel(KEY, chat_id)
    r.hdel("medbot:last_seen", chat_id)


def ack_once(chat_id: int, ttl_seconds: int = 24 * 3600) -> bool:
    """
    Вернёт True, если квиток ещё не отправляли.
    Ставит одноразовый ключ в Redis с истечением.
    """
    key = f"medbot:ack:{chat_id}"
    # SET NX EX: поставить, только если не существует
    return bool(r.set(key, "1", nx=True, ex=ttl_seconds))
