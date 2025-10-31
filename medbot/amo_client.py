# amo_client.py — изолированный клиент amoCRM (HTTP и Chat API)
# Новые/изменённые места помечены # 🔴. Комментарии короткие.

from __future__ import annotations  # типы из будущего

import os  # окружение и пути
import json  # сериализация
import hmac  # подпись Chat API
import hashlib  # MD5/HMAC
import logging  # логи
from pathlib import Path  # путь к .env
from typing import Optional  # типы
import aiohttp  # HTTP-клиент
import uuid
import time



from dotenv import load_dotenv  # .env загрузчик

from constants import AMO_REQUEST_TIMEOUT_SEC  # таймауты HTTP


# ======================
#    Окружение/пути
# ======================

ENV_PATH = "/var/www/medbot/.env"  # путь до .env на сервере
if os.path.exists(ENV_PATH):  # грузим .env если доступен
    load_dotenv(ENV_PATH)

AMO_API_URL = os.getenv("AMO_API_URL", "")  # базовый URL API
AMO_CLIENT_ID = os.getenv("AMO_CLIENT_ID", "")  # OAuth client_id
AMO_CLIENT_SECRET = os.getenv("AMO_CLIENT_SECRET", "")  # client_secret
AMO_REDIRECT_URI = os.getenv("AMO_REDIRECT_URI", "")  # redirect_uri
AMO_REFRESH_TOKEN = os.getenv("AMO_REFRESH_TOKEN", "")  # refresh
AMO_ACCESS_TOKEN = os.getenv("AMO_ACCESS_TOKEN", "")  # access
AMO_PIPELINE_ID = os.getenv("AMO_PIPELINE_ID", "0")  # ID воронки

# ======================
#     Token refresh
# ======================

async def refresh_access_token() -> str:
    """Обновляет access_token по refresh_token и перезаписывает .env."""
    url = f"{AMO_API_URL}/oauth2/access_token"  # точка OAuth
    payload = {  # тело запроса
        "client_id": AMO_CLIENT_ID,
        "client_secret": AMO_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": os.getenv("AMO_REFRESH_TOKEN", AMO_REFRESH_TOKEN),
        "redirect_uri": AMO_REDIRECT_URI,
    }
    async with aiohttp.ClientSession() as s:  # HTTP-сессия
        async with s.post(  # POST на OAuth endpoint
            url, json=payload, timeout=AMO_REQUEST_TIMEOUT_SEC
        ) as r:
            text = await r.text()  # снимаем текст на случай ошибок
            if r.status != 200:  # неуспех → бросаем
                raise RuntimeError(
                    f"Token refresh failed [{r.status}]: {text}"
                )
            data = await r.json()  # JSON-ответ
            new_access = data["access_token"]  # новый access
            new_refresh = data.get(  # новый refresh (если пришёл)
                "refresh_token",
                os.getenv("AMO_REFRESH_TOKEN", AMO_REFRESH_TOKEN),
            )

    # перезаписываем .env атомарно (безопаснее, чем sed)
    env_path = Path(ENV_PATH)
    lines = env_path.read_text(encoding="utf-8").splitlines(True)
    out = []
    for line in lines:
        if line.startswith("AMO_ACCESS_TOKEN="):
            out.append(f"AMO_ACCESS_TOKEN={new_access}\n")
        elif line.startswith("AMO_REFRESH_TOKEN="):
            out.append(f"AMO_REFRESH_TOKEN={new_refresh}\n")
        else:
            out.append(line)
    env_path.write_text("".join(out), encoding="utf-8")

    # обновляем переменные процесса
    os.environ["AMO_ACCESS_TOKEN"] = new_access
    os.environ["AMO_REFRESH_TOKEN"] = new_refresh

    logging.info("✅ amoCRM token refreshed successfully")  # лог успеха
    return new_access  # возвращаем новый access

# ======================
#    Вспомогательные
# ======================

def _auth_header() -> dict[str, str]:
    """Заголовок Authorization для amoCRM."""
    token = os.getenv("AMO_ACCESS_TOKEN", AMO_ACCESS_TOKEN)
    return {"Authorization": f"Bearer {token}"}

# ======================
#  Создание контакта/сделки
# ======================

async def _create_contact(name: str) -> Optional[int]:
    """Создаёт контакт и возвращает contact_id."""
    url = f"{AMO_API_URL}/api/v4/contacts"
    payload = [{"name": name or "Telegram user"}]
    async with aiohttp.ClientSession() as s:
        async with s.post(
            url, headers=_auth_header(), json=payload
        ) as r:
            txt = await r.text()
            logging.info("📡 Contact resp [%s]: %s", r.status, txt)
            if r.status == 401:  # токен протух — обновим и повторим
                await refresh_access_token()
                return await _create_contact(name)
            if r.status != 200:
                return None
            data = await r.json()
    emb = data.get("_embedded", {}) if isinstance(data, dict) else {}
    arr = emb.get("contacts", [])
    return (arr[0] or {}).get("id") if arr else None


async def create_lead_in_amo(
    chat_id: int,
    username: str,
) -> Optional[int]:
    """Создаёт контакт и сделку в нужной воронке, связывает их,
    возвращает lead_id.
    """
    contact_id = await _create_contact(username)  # создаём контакт
    if not contact_id:
        return None

    # 🔴 безопасно читаем id воронки из .env (жёсткая привязка)
    try:
        pipeline_id = int(AMO_PIPELINE_ID)
    except Exception:
        pipeline_id = 0

    url = f"{AMO_API_URL}/api/v4/leads"  # endpoint сделок

    # формируем payload: имя + нужная воронка + привязка контакта
    payload = [{
        "name": f"Новый запрос из Telegram ({username})",
        "pipeline_id": pipeline_id or None,
        "_embedded": {"contacts": [{"id": contact_id}]},
    }]

    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=_auth_header(), json=payload) as r:
            txt = await r.text()
            logging.info("📡 Lead resp [%s]: %s", r.status, txt)
            if r.status == 401:  # токен протух — обновим и повторим
                await refresh_access_token()
                return await create_lead_in_amo(chat_id, username)
            if r.status != 200:
                return None
            data = await r.json()

    emb = data.get("_embedded", {}) if isinstance(data, dict) else {}
    arr = emb.get("leads", [])
    lead_id = (arr[0] or {}).get("id") if arr else None

    if lead_id:
        logging.info("✅ lead %s created for chat_id=%s", lead_id, chat_id)

        # 🔴 Переносим лид в воронку "Платный канал (ИИ-врач)"
        target_pipeline_id = int(os.getenv("AMO_PIPELINE_AI_ID", "10176698"))
        moved = await move_lead_to_pipeline(lead_id, target_pipeline_id)
        if moved:
            logging.info("✅ lead %s moved to pipeline %s",
                         lead_id, target_pipeline_id)
        else:
            logging.warning("⚠️ failed to move lead %s to pipeline %s",
                            lead_id, target_pipeline_id)

    return lead_id


# 🔽 вставить сразу после create_lead_in_amo

async def move_lead_to_pipeline(lead_id: int, pipeline_id: int) -> bool:
    """Переносит сделку в нужную воронку."""
    url = f"{AMO_API_URL}/api/v4/leads/{lead_id}"
    payload = {"pipeline_id": pipeline_id}

    async with aiohttp.ClientSession() as s:
        async with s.patch(url, headers=_auth_header(), json=payload) as r:
            txt = await r.text()
            logging.info("📦 Move lead resp [%s]: %s", r.status, txt)
            return 200 <= r.status < 300


# ======================
#     Заметки: файл
# ======================

async def add_file_note(lead_id: str, uuid: str,
                        file_name: str = "") -> bool:
    """Прикрепляет файл (uuid) к сделке как заметку-attachment."""
    url = f"{AMO_API_URL}/api/v4/leads/notes"
    payload = [{
        "entity_id": int(lead_id),
        "note_type": "attachment",
        "params": {"attachments": [{
            "file_name": file_name or "file.bin",
            "uuid": uuid,
        }]},
    }]
    async with aiohttp.ClientSession() as s:
        async with s.post(
            url, headers=_auth_header(), json=payload
        ) as r:
            txt = await r.text()
            ok = 200 <= r.status < 300
            logging.info("📎 add_file_note resp [%s]: %s", r.status, txt)
            if r.status == 401:
                await refresh_access_token()
                return await add_file_note(lead_id, uuid, file_name)
            return ok

# ======================
#     Chat API (amojo)
# ======================

def _rfc1123_now_gmt() -> str:
    """Дата в RFC1123/GMT для заголовка Date."""
    from email.utils import formatdate
    return formatdate(usegmt=True)

def _md5_hex_lower(data: bytes) -> str:
    """MD5 от байтов в hex нижним регистром."""
    return hashlib.md5(data).hexdigest().lower()

def _hmac_sha1_hex_ascii(src: str, secret_ascii: str) -> str:
    """HMAC-SHA1(src) ключом-строкой, hex lower."""
    mac = hmac.new(secret_ascii.encode("utf-8"),
                   src.encode("utf-8"),
                   hashlib.sha1)
    return mac.hexdigest().lower()

async def send_chat_message_v2(  # 🔴
    scope_id: str,
    chat_id: int,
    text: str,
    username: Optional[str] = None,
) -> bool:
    """Отправка new_message в amojo (единая точка v2)."""
    secret = os.getenv("AMO_CHAT_SECRET", "")
    if not secret or not scope_id:
        logging.warning("⚠️ Chat v2: missing secret or scope_id")
        return False

    # 🔴 Идемпотентный msgid и метка времени (требуются в v2)
    import uuid  # 🔴
    import time  # 🔴
    msgid = uuid.uuid4().hex  # 🔴
    ts = int(time.time())  # 🔴

    # 🔴 Полностью согласно PHP-примеру amoCRM: event_type + payload
    body = {  # 🔴
        "event_type": "new_message",  # 🔴
        "payload": {  # 🔴
            "timestamp": ts,  # 🔴
            "conversation_id": f"tg_{chat_id}",  # 🔴
            "silent": False,  # 🔴
            "msgid": msgid,  # 🔴
            "sender": {  # 🔴
                "id": str(chat_id),  # 🔴
                "name": username or f"User {chat_id}",  # 🔴
            },  # 🔴
            "message": {  # 🔴
                "type": "text",  # 🔴
                "text": (text or "")[:4000],  # 🔴
            },  # 🔴
        },  # 🔴
    }  # 🔴

    body_bytes = json.dumps(
        body, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    content_md5 = _md5_hex_lower(body_bytes)
    content_type = "application/json"
    date_gmt = _rfc1123_now_gmt()

    # 🔴 Единая точка входа (без /chats, без /chats/link)
    path = f"/v2/origin/custom/{scope_id}"  # 🔴
    sign_src = "\n".join([
        "POST", content_md5, content_type, date_gmt, path
    ])
    signature = _hmac_sha1_hex_ascii(sign_src, secret)

    url = f"https://amojo.amocrm.ru{path}"
    try:
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
