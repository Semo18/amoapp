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
    
# amo_client.py — заменить функцию целиком
# =======================================
#      🧩 amoCRM Chat API (origin/custom)
# =======================================

# --- helpers (оставьте рядом с остальными) -------------------------------


def _rfc2822_now_utc() -> str:
    """RFC2822-тimestamp в UTC для заголовка Date (пример amo)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%a, %d %b %Y %H:%M:%S +0000")  # без 'GMT'


def _md5_hex_lower(data: bytes) -> str:
    """MD5 тела, hex lower."""
    return hashlib.md5(data).hexdigest().lower()


def _md5_base64(data: bytes) -> str:
    """MD5 тела, base64 (некоторые кластеры amojo требуют именно это)."""
    dig = hashlib.md5(data).digest()
    return base64.b64encode(dig).decode("ascii")


def _hmac_sha1_hex(src: str, secret: str) -> str:
    """HMAC-SHA1(src, secret) → hex lower."""
    return hmac.new(secret.encode("utf-8"),
                    src.encode("utf-8"),
                    hashlib.sha1).hexdigest().lower()

# --- отправка (замените тело функции; комментарии высокоуровневые) -------


async def send_chat_message_v2(
    scope_id: str,
    chat_id: int,
    text: str,
    username: str | None = None,
) -> bool:
    """
    Отправка new_message в Chat API (origin/custom) с авто-ретраем:
    1) Плотный JSON (без пробелов), snake+camel дубли ключей,
       user+sender — чтоб пройти разные валидаторы.
    2) Пытаемся с MD5 hex → если 400/VALIDATION_ERROR, ретраим с MD5 base64.
    3) Подпись собираем ровно: METHOD, MD5, Content-Type, Date(RFC2822), path.
    """

    secret = os.getenv("AMO_CHAT_SECRET", "")
    if not secret or not scope_id:
        logging.warning("⚠️ Chat v2: secret/scope отсутствуют")
        return False

    cid = f"tg_{chat_id}"  # внешний ID диалога в нашей системе

    # — минимально валидное тело + «двойные» ключи для совместимости —
    payload = {
        "event_type": "new_message",
        "payload": {
            "conversation_id": cid,
            "conversationId": cid,
            "conversation": {"id": cid},
            "message": {"type": "text", "text": (text or "")[:4000]},
            "user": {"id": str(chat_id), "name": (username or f"User {chat_id}")[:128]},
            "sender": {"id": str(chat_id), "name": (username or f"User {chat_id}")[:128]},
        },
    }

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    body_bytes = body.encode("utf-8")

    path = f"/v2/origin/custom/{scope_id}/chats"
    url = f"https://amojo.amocrm.ru{path}"
    ctype = "application/json"

    async def _post_with(md5_value: str) -> tuple[int, str]:
        """Собираем подпись под указанный MD5 и шлём."""
        date_hdr = _rfc2822_now_utc()  # пример из доков — RFC2822 +0000
        sign_src = "\n".join(["POST", md5_value, ctype, date_hdr, path])
        sign = _hmac_sha1_hex(sign_src, secret)

        # Диагностический лог — поможет, если подпись не зайдёт
        logging.info("🔐 ChatAPI v2 sign src: %s", sign_src)

        async with aiohttp.ClientSession() as s:
            async with s.post(
                url,
                data=body_bytes,
                headers={
                    "Date": date_hdr,
                    "Content-Type": ctype,
                    "Content-MD5": md5_value,
                    "X-Signature": sign,
                    "Accept": "application/json",
                },
                timeout=AMO_REQUEST_TIMEOUT_SEC,
            ) as r:
                return r.status, await r.text()

    # Отладочный payload (усечённый текст)
    try:
        dbg = json.loads(body)
        dbg["payload"]["message"]["text"] = dbg["payload"]["message"]["text"][:120]
        logging.info("💬 ChatAPI v2 payload: %s", json.dumps(dbg, ensure_ascii=False))
    except Exception:
        pass

    # 1-я попытка: MD5 hex
    md5_hex = _md5_hex_lower(body_bytes)
    st, txt = await _post_with(md5_hex)
    logging.info("💬 ChatAPI v2 send [hex][%s]: %s", st, txt)
    if 200 <= st < 300:
        return True

    # Если сервер «не видит» поля — пробуем MD5 base64 (частый кейс)
    if st == 400 and "VALIDATION_ERROR" in txt and (
        "ConversationId" in txt or "User" in txt
    ):
        md5_b64 = _md5_base64(body_bytes)
        st2, txt2 = await _post_with(md5_b64)
        logging.info("💬 ChatAPI v2 send [b64][%s]: %s", st2, txt2)
        return 200 <= st2 < 300

    return False
