# amo_client.py
# 🔴 Подсистема интеграции с amoCRM:
# - автообновление токена по refresh_token
# - создание контактов и сделок
# - добавление примечаний к существующим сделкам
# - сохранение связки chat_id → lead_id в Redis

import os, aiohttp, asyncio, logging
from dotenv import load_dotenv
from pathlib import Path
from storage import set_lead_id, get_lead_id  # 🔴 связь chat_id → lead_id
from typing import Optional
from constants import AMO_REQUEST_TIMEOUT_SEC  # 🔴 таймаут для amoCRM API
import hashlib  # для Content-MD5  # noqa: E402
import hmac     # для HMAC-SHA1 подписи  # noqa: E402
import base64   # иногда удобно, но тут не используем  # noqa: E402
import datetime # для заголовка Date  # noqa: E402
import json     # сериализация тела запроса  # noqa: E402
import binascii  # 🔴 для hex→bytes



# =============================
#        НАСТРОЙКА ОКРУЖЕНИЯ
# =============================

ENV_PATH = "/var/www/medbot/.env"  # абсолютный путь к .env на сервере
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
#     🔁  ОБНОВЛЕНИЕ ACCESS TOKEN
# =======================================

async def refresh_access_token() -> str:
    """🔁 Обновляет токен amoCRM через refresh_token и сохраняет в .env."""
    url = f"{AMO_API_URL}/oauth2/access_token"
    payload = {
        "client_id": AMO_CLIENT_ID,
        "client_secret": AMO_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": AMO_REFRESH_TOKEN,
        "redirect_uri": AMO_REDIRECT_URI,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=AMO_REQUEST_TIMEOUT_SEC) as resp:  # 🔴
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Token refresh failed [{resp.status}]: {text}")

            data = await resp.json()
            new_token = data["access_token"]
            new_refresh = data.get("refresh_token", AMO_REFRESH_TOKEN)

            # 🔴 перезаписываем токены в .env
            lines = []
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("AMO_ACCESS_TOKEN="):
                        line = f"AMO_ACCESS_TOKEN={new_token}\n"
                    elif line.startswith("AMO_REFRESH_TOKEN="):
                        line = f"AMO_REFRESH_TOKEN={new_refresh}\n"
                    lines.append(line)
            with open(ENV_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines)

            # обновляем переменные окружения в памяти процесса
            os.environ["AMO_ACCESS_TOKEN"] = new_token
            os.environ["AMO_REFRESH_TOKEN"] = new_refresh

            logging.info("✅ amoCRM token refreshed successfully")
            return new_token


# =======================================
#      🔧 СОЗДАНИЕ КОНТАКТА + СДЕЛКИ
# =======================================

# 🔁 создание сделки и контакта
async def create_lead_in_amo(chat_id: int, username: str) -> str | None:
    """Создаёт сделку и контакт в amoCRM, возвращает lead_id."""
    access_token = os.getenv("AMO_ACCESS_TOKEN")
    if not access_token:
        logging.warning("⚠️ No AMO_ACCESS_TOKEN in env")
        return None

    try:
        async with aiohttp.ClientSession() as s:
            # 🔹 создаём контакт
            contact = {"name": username or f"Telegram {chat_id}"}
            async with s.post(
                f"{AMO_API_URL}/api/v4/contacts",
                headers={"Authorization": f"Bearer {access_token}"},
                json=[contact],
            ) as r:
                txt = await r.text()
                logging.info(f"📡 Contact resp [{r.status}]: {txt}")
                if r.status != 200:
                    if r.status == 401:
                        logging.warning("⚠️ Token expired during contact creation — refreshing...")
                        await refresh_access_token()
                        return await create_lead_in_amo(chat_id, username)
                    logging.warning(f"❌ Contact creation failed [{r.status}]: {txt}")
                    return None
                res = await r.json()
                # новый формат ответа amoCRM — id внутри _embedded
                contact_id = None
                if isinstance(res, dict):
                    embedded = res.get("_embedded", {})
                    contacts = embedded.get("contacts", [])
                    if contacts and isinstance(contacts, list):
                        contact_id = contacts[0].get("id")

                if not contact_id:
                    logging.warning(f"⚠️ Could not parse contact_id from response: {res}")
                    return None

            # 🔹 создаём сделку
            lead = {
                "name": f"Новый запрос из Telegram ({username})",
                "pipeline_id": int(AMO_PIPELINE_ID),
                "_embedded": {"contacts": [{"id": contact_id}]},
            }
            async with s.post(
                f"{AMO_API_URL}/api/v4/leads",
                headers={"Authorization": f"Bearer {access_token}"},
                json=[lead],
            ) as r:
                txt = await r.text()
                logging.info(f"📡 Lead resp [{r.status}]: {txt}")
                if r.status == 401:
                    logging.warning("⚠️ Token expired during lead creation — refreshing...")
                    await refresh_access_token()
                    return await create_lead_in_amo(chat_id, username)
                if r.status != 200:
                    logging.warning(f"❌ Lead creation failed [{r.status}]: {txt}")
                    return None
                data = await r.json()
                lead_id = None
                if isinstance(data, dict):
                    embedded = data.get("_embedded", {})
                    leads = embedded.get("leads", [])
                    if leads and isinstance(leads, list):
                        lead_id = leads[0].get("id")

                if not lead_id:
                    logging.warning(f"⚠️ Could not parse lead_id from response: {data}")
                    return None

                logging.info(f"✅ Created amoCRM lead {lead_id} for chat_id={chat_id}")
                return lead_id

    except Exception as e:
        logging.warning(f"⚠️ Exception in create_lead_in_amo: {e}")
        import traceback
        logging.warning(traceback.format_exc())
        return None

# amo_client.py — добавить в конец файла
async def add_text_note(lead_id: str, text: str) -> bool:
    """
    Добавляет текстовую заметку к сделке.
    """
    access_token = os.getenv("AMO_ACCESS_TOKEN")
    if not access_token:
        logging.warning("⚠️ No AMO_ACCESS_TOKEN in env")
        return False

    payload = [{
        "entity_id": int(lead_id),
        "note_type": "common",
        "params": {"text": text[:8000]},  # защитимся от слишком длинного
    }]

    url = f"{AMO_API_URL}/api/v4/leads/notes"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            ) as r:
                if r.status == 401:
                    await refresh_access_token()
                    return await add_text_note(lead_id, text)
                txt = await r.text()
                ok = 200 <= r.status < 300
                logging.info(f"📎 add_text_note resp [{r.status}]: {txt}")
                return ok
    except Exception as e:
        logging.warning(f"⚠️ add_text_note exception: {e}")
        return False


async def add_file_note(lead_id: str, uuid: str, file_name: str = "") -> bool:
    """
    Прикрепляет ранее загруженный файл (uuid) как заметку-attachment к сделке.
    """
    access_token = os.getenv("AMO_ACCESS_TOKEN")
    if not access_token:
        logging.warning("⚠️ No AMO_ACCESS_TOKEN in env")
        return False

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

    url = f"{AMO_API_URL}/api/v4/leads/notes"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            ) as r:
                if r.status == 401:
                    await refresh_access_token()
                    return await add_file_note(lead_id, uuid, file_name)
                txt = await r.text()
                ok = 200 <= r.status < 300
                logging.info(f"📎 add_file_note resp [{r.status}]: {txt}")
                return ok
    except Exception as e:
        logging.warning(f"⚠️ add_file_note exception: {e}")
        return False
    
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