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

# =======================================
#      🧩 amoCRM Chat API (двусторонняя интеграция)
# =======================================

async def send_chat_message_to_amo(chat_id: int, text: str, username: str) -> bool:
    """
    Отправляет сообщение клиента в amoCRM как chat message (а не note).
    chat_id — ID Telegram-пользователя.
    """
    access_token = os.getenv("AMO_ACCESS_TOKEN")
    if not access_token:
        logging.warning("⚠️ No AMO_ACCESS_TOKEN in env")
        return False

    chat_uid = f"telegram-{chat_id}"  # уникальный chat_id amoCRM
    payload = {
        "add": [
            {
                "chat_id": chat_uid,
                "message": {
                    "text": text,
                    "type": "text",
                    "external_id": f"tg_{chat_id}_{int(asyncio.get_event_loop().time())}",
                },
                "user": {"id": str(chat_id), "name": username or f"User {chat_id}"},
            }
        ]
    }

    url = f"{AMO_API_URL}/api/v4/chats/messages"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=AMO_REQUEST_TIMEOUT_SEC,
            ) as r:
                txt = await r.text()
                ok = 200 <= r.status < 300
                logging.info(f"💬 send_chat_message_to_amo [{r.status}]: {txt}")
                if r.status == 401:
                    await refresh_access_token()
                    return await send_chat_message_to_amo(chat_id, text, username)
                return ok
    except Exception as e:
        logging.warning(f"⚠️ send_chat_message_to_amo exception: {e}")
        return False

def _rfc1123_now_gmt() -> str:
    """
    Возвращает текущее время в формате RFC1123 (GMT).
    Этот формат требуется заголовку Date в Chat API.
    """
    return datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")


def _hmac_sha1_hex(data: str, secret: str) -> str:
    """
    Возвращает нижний регистр hex HMAC-SHA1.
    Подпись идёт в заголовок X-Signature.
    """
    mac = hmac.new(secret.encode("utf-8"),
                   data.encode("utf-8"),
                   digestmod="sha1")
    return mac.hexdigest().lower()


def _md5_hex_lower(payload_bytes: bytes) -> str:
    """
    MD5 от тела запроса в нижнем регистре — для заголовка Content-MD5.
    Важно считать по байтам, включая возможные \n в конце.
    """
    return hashlib.md5(payload_bytes).hexdigest().lower()


async def send_chat_message_v2(scope_id: str,
                               chat_id: int,
                               text: str,
                               username: str | None = None) -> bool:
    """
    Отправляет событие new_message в Chat API (amojo) для созданного канала.

    Стратегия:
    1) Сформировать body события "new_message" (conversation_id — наш tg_id).
    2) Посчитать Content-MD5 по bytes(body).
    3) Собрать строку для подписи: METHOD, MD5, Content-Type, Date, path.
    4) Подписать HMAC-SHA1 секретом канала → X-Signature.
    5) POST на amojo.amocrm.ru по пути /v2/origin/custom/{scope_id}/chats.
    """

    # Берём секрет и (опционально) код канала из окружения
    secret = os.getenv("AMO_CHAT_SECRET", "")
    if not secret:
        logging.warning("⚠️ AMO_CHAT_SECRET is empty")
        return False

    # Конструируем conversation_id: достаточно стабильной строки
    # Здесь привязываемся к Telegram chat_id, чтобы «нить» чата была одна.
    conversation_id = f"tg_{chat_id}"

    # Собираем тело события Chat API
    body = {
        "event_type": "new_message",
        "payload": {
            "conversation_id": conversation_id,
            "message": {
                "text": text[:4000],  # бережёмся от слишком длинных
            },
            # Можно добавить "sender" при необходимости
            # "sender": {"id": str(chat_id), "name": username or "User"},
        },
    }
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")

    # Готовим заголовки
    content_type = "application/json"
    content_md5 = _md5_hex_lower(body_bytes)
    date_gmt = _rfc1123_now_gmt()

    # Путь для подписи и для URL — строго без домена/GET-параметров
    path = f"/v2/origin/custom/{scope_id}/chats"

    # Формируем строку подписи (порядок важен по документации)
    sign_str = "\n".join([
        "POST",             # метод капсом
        content_md5,        # Content-MD5
        content_type,       # Content-Type
        date_gmt,           # Date
        path,               # путь запроса
    ])
    signature = _hmac_sha1_hex(sign_str, secret)

    # Отправляем запрос в amojo
    url = f"https://amojo.amocrm.ru{path}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                url,
                data=body_bytes,  # отправляем bytes, чтобы MD5 совпал
                headers={
                    "Content-Type": content_type,
                    "Content-MD5": content_md5,
                    "Date": date_gmt,
                    "X-Signature": signature,
                },
                timeout=AMO_REQUEST_TIMEOUT_SEC,
            ) as r:
                txt = await r.text()
                ok = 200 <= r.status < 300
                logging.info("💬 ChatAPI v2 send [%s]: %s", r.status, txt)
                return ok
    except Exception as e:
        logging.warning("⚠️ ChatAPI v2 send exception: %s", e)
        return False
