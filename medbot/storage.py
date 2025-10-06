# storage.py
import os, time  # os — работа с окружением, time — текущее время (timestamp)
import redis  # библиотека для подключения к Redis (быстрая база данных в памяти)

# берём адрес Redis из переменной окружения, если её нет — используем локальный сервер
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# создаём подключение к Redis, decode_responses=True → все ответы будут строками, а не байтами
r = redis.from_url(REDIS_URL, decode_responses=True)

# ключ в Redis, где мы храним связку chat_id → thread_id
KEY = "medbot:tchat:thread"

# локальный словарь для хранения времени последнего сообщения (используется для оптимизаций)
_last_seen = {}  # chat_id -> timestamp последнего входящего сообщения


def get_thread_id(chat_id: int):
    # получаем из Redis сохранённый thread_id для чата (или None, если нет)
    return r.hget(KEY, chat_id)


def set_thread_id(chat_id: int, thread_id: str):
    # сохраняем в Redis: для данного chat_id → thread_id
    r.hset(KEY, chat_id, thread_id)
    # параллельно сохраняем время последней активности, чтобы потом можно было чистить старые данные
    r.hset("medbot:last_seen", chat_id, int(time.time()))


def drop_thread_id(chat_id: int):
    # удаляем связку chat_id → thread_id из Redis
    r.hdel(KEY, chat_id)
    # и время последней активности этого чата
    r.hdel("medbot:last_seen", chat_id)


def ack_once(chat_id: int, ttl_seconds: int = 24 * 3600) -> bool:
    """
    Проверяет, отправляли ли уже авто-квиток.
    Если ещё не отправляли — возвращает True и помечает в Redis, что отправлен.
    Хранение пометки ограничено по времени (по умолчанию 24 часа).
    """
    key = f"medbot:ack:{chat_id}"  # формируем ключ для Redis
    # команда SET NX EX: записать значение, только если ключа ещё нет, и задать срок жизни
    return bool(r.set(key, "1", nx=True, ex=ttl_seconds))


def should_ack(chat_id: int, cooldown_sec: int = 3600) -> bool:
    """
    Решает, нужно ли снова отправить авто-квиток (например, "Ваш запрос принят").
    Возвращает True, если прошло больше заданного времени (по умолчанию 1 час).
    """
    key = f"medbot:last_ack:{chat_id}"  # ключ в Redis для отметки последнего авто-квитка
    now = int(time.time())  # текущее время
    last = r.get(key)  # время предыдущего авто-квитка
    if last is None or now - int(last) > cooldown_sec:  # если ещё не было или прошло достаточно времени
        r.set(key, now)  # обновляем время последнего авто-квитка
        return True  # можно отправить новый авто-квиток
    return False  # рано отправлять, ждём ещё
