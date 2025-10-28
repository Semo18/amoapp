 amo_client.py (фрагменты, ключевые изменения)

import os, aiohttp, asyncio, logging, json, hashlib, hmac, binascii
from dotenv import load_dotenv
from pathlib import Path
from typing import Optional
from constants import AMO_REQUEST_TIMEOUT_SEC
from storage import set_lead_id, get_lead_id  # связь chat_id → lead_id
import datetime

ENV_PATH = "/var/www/medbot/.env"
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)

AMO_API_URL = os.getenv("AMO_API_URL", "")
AMO_CLIENT_ID = os.getenv("AMO_CLIENT_ID", "")
AMO_CLIENT_SECRET = os.getenv("AMO_CLIENT_SECRET", "")
AMO_REDIRECT_URI = os.getenv("AMO_REDIRECT_URI", "")
AMO_REFRESH_TOKEN = os.getenv("AMO_REFRESH_TOKEN", "")
AMO_ACCESS_TOKEN = os.getenv("AMO_ACCESS_TOKEN", "")
AMO_PIPELINE_ID = os.getenv("AMO_PIPELINE_ID", "0")


# =======================================
#      🔐 Менеджер OAuth-токена
# =======================================

class TokenManager:
    """Высокоуровневый менеджер токенов с auto-refresh и записью в .env."""
    _lock = asyncio.Lock()  # 🔴 защита от одновременного рефреша

    @staticmethod
    def _read_env() -> dict:
        """Считывает актуальные значения из .env (файл — источник истины)."""
        env = {}
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    env[k] = v
        return env

    @staticmethod
    def _write_env(upd: dict) -> None:
        """Перезаписывает пары ключ=значение в .env (остальное сохраняем)."""
        lines = []
        seen = set()
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, _ = line.rstrip("\n").split("=", 1)
                    if k in upd:
                        lines.append(f"{k}={upd[k]}\n")
                        seen.add(k)
                        continue
                lines.append(line)
        for k, v in upd.items():
            if k not in seen:
                lines.append(f"{k}={v}\n")
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)

    @classmethod
    async def refresh(cls) -> str:
        """🔁 Обновляет access/refresh токены и сохраняет их в .env и env."""
        async with cls._lock:  # 🔴 только один поток делает refresh
            env = cls._read_env()
            url = f"{AMO_API_URL}/oauth2/access_token"
            payload = {
                "client_id": AMO_CLIENT_ID,
                "client_secret": AMO_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": env.get("AMO_REFRESH_TOKEN", ""),
                "redirect_uri": AMO_REDIRECT_URI,
            }
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    url, json=payload, timeout=AMO_REQUEST_TIMEOUT_SEC
                ) as r:
                    txt = await r.text()
                    if r.status != 200:
                        raise RuntimeError(
                            f"Token refresh failed [{r.status}]: {txt}"
                        )
                    data = await r.json()
            new_access = data["access_token"]
            new_refresh = data.get("refresh_token",
                                   env.get("AMO_REFRESH_TOKEN", ""))
            cls._write_env({
                "AMO_ACCESS_TOKEN": new_access,
                "AMO_REFRESH_TOKEN": new_refresh,
            })
            os.environ["AMO_ACCESS_TOKEN"] = new_access
            os.environ["AMO_REFRESH_TOKEN"] = new_refresh
            logging.info("✅ amoCRM token refreshed and persisted")
            return new_access

    @classmethod
    async def bearer(cls) -> str:
        """Возвращает актуальный токен из файла/окружения (без refresh)."""
        token = os.getenv("AMO_ACCESS_TOKEN", "")
        if token:
            return token
        env = cls._read_env()
        token = env.get("AMO_ACCESS_TOKEN", "")
        if token:
            os.environ["AMO_ACCESS_TOKEN"] = token
        return token


# =======================================
#   🔧 Универсальный враппер REST-запросов
# =======================================

async def amo_request(method: str, path: str, **kw) -> aiohttp.ClientResponse:
    """
    Делает REST-запрос c Bearer и авто-рефрешем на 401.
    Стратегия:
      1) Пытаемся с текущим токеном.
      2) Если 401 — один раз делаем refresh и повторяем.
    """
    assert path.startswith("/"), "path должен начинаться с '/'"
    url = f"{AMO_API_URL}{path}"

    token = await TokenManager.bearer()
    headers = kw.pop("headers", {})
    headers = {"Authorization": f"Bearer {token}", **headers}
    timeout = kw.pop("timeout", AMO_REQUEST_TIMEOUT_SEC)

    async with aiohttp.ClientSession() as s:
        async with s.request(method, url, headers=headers,
                             timeout=timeout, **kw) as r:
            if r.status != 401:
                return r
            # 401: пробуем обновиться и повторить
            logging.info("🔁 401 from amoCRM → refreshing token...")
        await TokenManager.refresh()
        token = await TokenManager.bearer()
        headers["Authorization"] = f"Bearer {token}"
        async with aiohttp.ClientSession() as s2:
            return await s2.request(method, url, headers=headers,
                                    timeout=timeout, **kw)


# =======================================================
#      🧩 CRUD по вашим сценариям(Создание слеки/заметки)
# =======================================================

async def create_lead_in_amo(chat_id: int, username: str) -> str | None:
    """Создаёт контакт и сделку. Повтор на 401 делает amo_request."""
    # контакт
    contact_payload = [{"name": username or f"Telegram {chat_id}"}]
    r = await amo_request("POST", "/api/v4/contacts", json=contact_payload)
    txt = await r.text()
    logging.info("📡 Contact resp [%s]: %s", r.status, txt)
    if r.status != 200:
        return None
    data = await r.json()
    contact_id = (data.get("_embedded", {})
                      .get("contacts", [{}])[0].get("id"))
    if not contact_id:
        return None

    # сделка
    lead_payload = [{
        "name": f"Новый запрос из Telegram ({username})",
        "pipeline_id": int(AMO_PIPELINE_ID),
        "_embedded": {"contacts": [{"id": contact_id}]},
    }]
    r = await amo_request("POST", "/api/v4/leads", json=lead_payload)
    txt = await r.text()
    logging.info("📡 Lead resp [%s]: %s", r.status, txt)
    if r.status != 200:
        return None
    data = await r.json()
    lead_id = (data.get("_embedded", {})
                   .get("leads", [{}])[0].get("id"))
    if not lead_id:
        return None
    logging.info("✅ Created lead %s for chat_id=%s", lead_id, chat_id)
    return str(lead_id)


async def add_text_note(lead_id: str, text: str) -> bool:
    """Добавляет текстовую заметку (с авто-рефрешем токена)."""
    payload = [{
        "entity_id": int(lead_id),
        "note_type": "common",
        "params": {"text": text[:8000]},
    }]
    r = await amo_request("POST", "/api/v4/leads/notes", json=payload)
    txt = await r.text()
    ok = 200 <= r.status < 300
    logging.info("📎 add_text_note resp [%s]: %s", r.status, txt)
    return ok


async def add_file_note(lead_id: str, uuid: str, file_name: str = "") -> bool:
    """Прикрепляет файл (uuid) как заметку-attachment."""
    payload = [{
        "entity_id": int(lead_id),
        "note_type": "attachment",
        "params": {
            "attachments": [{
                "file_name": file_name or "file.bin",
                "uuid": uuid,
            }]
        },
    }]
    r = await amo_request("POST", "/api/v4/leads/notes", json=payload)
    txt = await r.text()
    ok = 200 <= r.status < 300
    logging.info("📎 add_file_note resp [%s]: %s", r.status, txt)
    return ok
    
# amo_client.py — заменить функцию целиком
# =======================================
#      🧩 amoCRM Chat API (origin/custom)
# =======================================

# --- helpers (оставьте рядом с остальными) -------------------------------


def _md5_hex_lower(data: bytes) -> str:
    """
    Считает MD5 от байтов и возвращает hex в нижнем регистре.
    Используем hex, т.к. именно так сервер валидирует подпись в нашем
    аккаунте (при base64 подпись принималась, но менялась ошибка).
    """
    return hashlib.md5(data).hexdigest().lower()


def _rfc1123_now_gmt() -> str:
    """
    Возвращает дату в формате RFC1123 (с 'GMT'), как требует amojo.
    """
    from email.utils import formatdate
    return formatdate(usegmt=True)


def _hmac_sha1_hex_ascii(src: str, secret_ascii: str) -> str:
    """
    HMAC-SHA1(src, key) в hex lower.

    Стратегия:
    - Ключ трактуем как ASCII-строку.
      Это важно: в нашем канале секрет принимается как ASCII, а не как
      hex-строка, иначе сервер возвращает ORIGIN_INVALID_SIGNATURE (403).
    """
    key = secret_ascii.encode("utf-8")  # 🔴 ключ как ascii-строка
    mac = hmac.new(key, src.encode("utf-8"), hashlib.sha1)
    return mac.hexdigest().lower()


async def send_chat_message_v2(
    scope_id: str,
    chat_id: int,
    text: str,
    username: Optional[str] = None,
) -> bool:
    """
    Отправка 'new_message' в Chat API (amojo) для подключённого scope.

    Общая стратегия:
    1) Поля 'conversation_id' и 'user' кладём на верхний уровень.
       В 'payload' передаём только 'message'. Так требует валидатор.
    2) Content-MD5 считаем как hex от тела (без финального '\n').
    3) Собираем строку подписи (METHOD, MD5-hex, Content-Type, Date, path).
    4) Подписываем HMAC-SHA1 c ASCII-секретом канала.
    5) POST на https://amojo.amocrm.ru/v2/origin/custom/{scope_id}/chats.
    """

    secret = os.getenv("AMO_CHAT_SECRET", "")
    if not secret:
        logging.warning("⚠️ Chat v2: no AMO_CHAT_SECRET in env")
        return False
    if not scope_id:
        logging.warning("⚠️ Chat v2: empty scope_id")
        return False

    # --- формируем минимально валидное событие (см. 1) выше) ---
    body = {
        "event_type": "new_message",
        # эти два поля — на верхнем уровне, не внутри payload  # 🔴
        "conversation_id": f"tg_{chat_id}",                   # 🔴
        "user": {                                              # 🔴
            "id": str(chat_id),
            "name": username or f"User {chat_id}",
        },
        # собственно полезные данные события хранятся в payload
        "payload": {
            "message": {
                "type": "text",
                "text": (text or "")[:4000],
            }
        },
    }

    # сериализуем без лишних пробелов и переводов строки
    body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    content_type = "application/json"
    content_md5 = _md5_hex_lower(body_bytes)  # hex-формат MD5  # 🔴
    date_gmt = _rfc1123_now_gmt()
    path = f"/v2/origin/custom/{scope_id}/chats"

    # строка подписи — порядок и регистр строго фиксированы
    sign_src = "\n".join(
        ["POST", content_md5, content_type, date_gmt, path]
    )
    signature = _hmac_sha1_hex_ascii(sign_src, secret)  # 🔴 ASCII-ключ

    url = f"https://amojo.amocrm.ru{path}"
    try:
        # Логируем полезную нагрузку для отладки схемы (без секрета)
        logging.info("💬 ChatAPI v2 payload(top): %s", body)

        async with aiohttp.ClientSession() as s:
            async with s.post(
                url,
                data=body_bytes,
                headers={
                    "Date": date_gmt,
                    "Content-Type": content_type,
                    "Content-MD5": content_md5,
                    "X-Signature": signature,
                },
                timeout=AMO_REQUEST_TIMEOUT_SEC,
            ) as r:
                txt = await r.text()
                logging.info("💬 ChatAPI v2 send [%s]: %s", r.status, txt)
                return 200 <= r.status < 300
    except Exception as exc:
        logging.warning("⚠️ ChatAPI v2 send exception: %s", exc)
        return False