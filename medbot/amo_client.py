# -*- coding: utf-8 -*-
"""
Подсистема интеграции с amoCRM.

Возможности:
- Надёжная работа с OAuth2 токенами: JSON-кэш, file-lock, авто-ретрай 401.
- Создание контактов/сделок, добавление заметок.
- Отправка сообщений в Chat API v2 (origin/custom).
- Поддержка .env перезаписи (access/refresh) + обновление os.environ.

Стратегия токенов (вариант 2 — «ленивый рефреш»):
- Ничего не делаем на старте.
- Любой запрос идёт с текущим access_token.
- Если получаем 401 — один раз делаем refresh и повторяем запрос.
"""

from __future__ import annotations

import asyncio
import base64  # пригодится при расширениях
import binascii
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import aiohttp
from dotenv import load_dotenv

from constants import AMO_REQUEST_TIMEOUT_SEC
from storage import get_lead_id, set_lead_id  # связь chat_id ↔ lead_id


# ---------------------------
#   Загрузка окружения .env
# ---------------------------

ENV_PATH = "/var/www/medbot/.env"
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)

AMO_API_URL = os.getenv("AMO_API_URL", "").rstrip("/")
AMO_CLIENT_ID = os.getenv("AMO_CLIENT_ID", "")
AMO_CLIENT_SECRET = os.getenv("AMO_CLIENT_SECRET", "")
AMO_REDIRECT_URI = os.getenv("AMO_REDIRECT_URI", "")
AMO_PIPELINE_ID = int(os.getenv("AMO_PIPELINE_ID", "0"))

# Chat API (origin/custom)
AMO_CHAT_SECRET = os.getenv("AMO_CHAT_SECRET", "")
AMO_CHAT_SCOPE_ID = os.getenv("AMO_CHAT_SCOPE_ID", "")

# ---------------------------
#     Пути/каталоги кэша
# ---------------------------

RUNTIME_DIR = Path("/var/www/medbot/runtime")
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

TOKENS_CACHE = RUNTIME_DIR / "amo_tokens.json"
TOKENS_LOCK = asyncio.Lock()  # process-local lock (от гонок в одном процессе)


# ======================================================================
#                         Менеджер токенов
# ======================================================================

class AmoTokenManager:
    """
    Менеджер токенов:
    - хранит access/refresh в JSON-кэше (runtime),
    - дублирует актуальные значения в .env,
    - умеет делать refresh c file-safe перезаписью,
    - отдаёт access_token без рефреша (лениво).
    """

    def __init__(self,
                 env_path: str = ENV_PATH,
                 cache_path: Path = TOKENS_CACHE) -> None:
        self._env_path = env_path
        self._cache_path = cache_path

    # --- низкоуровневые helpers -------------------------------------

    def _read_env_pairs(self) -> Dict[str, str]:
        """Читает .env построчно в dict (без переинтерпретации)."""
        pairs: Dict[str, str] = {}
        if not os.path.exists(self._env_path):
            return pairs
        with open(self._env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                pairs[k] = v
        return pairs

    def _write_env_pairs(self, pairs: Dict[str, str]) -> None:
        """Безопасно перезаписывает .env из dict (сохраняя порядок
        известных ключей вверху, остальное ниже как было)."""
        order = [
            "AMO_API_URL",
            "AMO_CLIENT_ID",
            "AMO_CLIENT_SECRET",
            "AMO_REDIRECT_URI",
            "AMO_ACCESS_TOKEN",
            "AMO_REFRESH_TOKEN",
            "AMO_PIPELINE_ID",
            "AMO_CHAT_SECRET",
            "AMO_CHAT_SCOPE_ID",
        ]
        existing = self._read_env_pairs()
        existing.update(pairs)
        lines: list[str] = []
        # Сначала — известные ключи в порядке:
        for k in order:
            if k in existing:
                lines.append(f"{k}={existing[k]}\n")
                existing.pop(k, None)
        # Затем — все прочие, как есть:
        for k, v in existing.items():
            lines.append(f"{k}={v}\n")
        tmp = self._env_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp, self._env_path)

    def _load_cache(self) -> Tuple[str, str]:
        """Возвращает (access, refresh) из JSON-кэша либо из окружения."""
        if self._cache_path.exists():
            try:
                with open(self._cache_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                return obj.get("access_token", ""), obj.get("refresh_token", "")
            except Exception:
                logging.warning("⚠️ tokens cache read failed, ignore")

        return os.getenv("AMO_ACCESS_TOKEN", ""), os.getenv(
            "AMO_REFRESH_TOKEN", ""
        )

    def _save_cache(self, access: str, refresh: str) -> None:
        """Атомарно пишет JSON-кэш с токенами."""
        tmp = self._cache_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {"access_token": access, "refresh_token": refresh},
                f,
                ensure_ascii=False,
            )
        os.replace(tmp, self._cache_path)

    # --- публичные методы -------------------------------------------

    def get_access_pair(self) -> Tuple[str, str]:
        """Возвращает (access, refresh) БЕЗ рефреша (лениво)."""
        access, refresh = self._load_cache()
        if not access:
            access = os.getenv("AMO_ACCESS_TOKEN", "")
        if not refresh:
            refresh = os.getenv("AMO_REFRESH_TOKEN", "")
        return access, refresh

    async def refresh_tokens(self) -> str:
        """
        Делает refresh_token → access/refresh.
        Атомарно обновляет: JSON-кэш, .env, os.environ.
        Возвращает новый access_token.
        """
        async with TOKENS_LOCK:
            _, refresh = self.get_access_pair()
            if not refresh:
                raise RuntimeError("No AMO_REFRESH_TOKEN to refresh")

            url = f"{AMO_API_URL}/oauth2/access_token"
            payload = {
                "client_id": AMO_CLIENT_ID,
                "client_secret": AMO_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "redirect_uri": AMO_REDIRECT_URI,
            }

            async with aiohttp.ClientSession() as s:
                async with s.post(
                    url,
                    json=payload,
                    timeout=AMO_REQUEST_TIMEOUT_SEC,
                ) as r:
                    body = await r.text()
                    if r.status != 200:
                        raise RuntimeError(
                            f"Token refresh failed [{r.status}]: {body}"
                        )
                    data = await r.json()

            new_access = data["access_token"]
            new_refresh = data.get("refresh_token", refresh)

            # JSON-кэш
            self._save_cache(new_access, new_refresh)
            # .env
            self._write_env_pairs(
                {
                    "AMO_ACCESS_TOKEN": new_access,
                    "AMO_REFRESH_TOKEN": new_refresh,
                }
            )
            # окружение процесса
            os.environ["AMO_ACCESS_TOKEN"] = new_access
            os.environ["AMO_REFRESH_TOKEN"] = new_refresh

            logging.info("✅ amoCRM token refreshed")
            return new_access


# singleton менеджер
TOKEN_MANAGER = AmoTokenManager()


# ======================================================================
#                    Универсальный вызов amoCRM API
# ======================================================================

async def amo_request(method: str,
                      path: str,
                      *,
                      params: Optional[Dict[str, Any]] = None,
                      json_body: Optional[Any] = None,
                      data: Optional[Any] = None) -> aiohttp.ClientResponse:
    """
    Выполняет запрос к amoCRM с авто-ретраем после 401.

    Стратегия:
    - Берём текущий access_token (без рефреша).
    - Если ответ 401 — один раз refresh + повтор запроса.
    """
    if not path.startswith("/"):
        path = "/" + path
    url = f"{AMO_API_URL}{path}"

    access, _ = TOKEN_MANAGER.get_access_pair()
    headers = {"Authorization": f"Bearer {access}"}

    async with aiohttp.ClientSession() as s:
        resp = await s.request(
            method.upper(),
            url,
            params=params,
            json=json_body,
            data=data,
            headers=headers,
            timeout=AMO_REQUEST_TIMEOUT_SEC,
        )
        if resp.status != 401:
            return resp

        # 401 — пробуем разовую попытку обновиться и повторить
        await resp.read()  # освободим соединение
        logging.warning("🔁 401 from amoCRM — refreshing tokens and retry ...")
        await TOKEN_MANAGER.refresh_tokens()

        access2, _ = TOKEN_MANAGER.get_access_pair()
        headers["Authorization"] = f"Bearer {access2}"
        return await s.request(
            method.upper(),
            url,
            params=params,
            json=json_body,
            data=data,
            headers=headers,
            timeout=AMO_REQUEST_TIMEOUT_SEC,
        )


# ======================================================================
#                Создание контакта / сделки и заметок
# ======================================================================

async def create_contact(name: str) -> Optional[int]:
    """Создаёт контакт, возвращает id или None."""
    payload = [{"name": name}]
    resp = await amo_request("POST", "/api/v4/contacts", json_body=payload)
    txt = await resp.text()
    logging.info("📡 create_contact [%s]: %s", resp.status, txt)
    if resp.status != 200:
        return None
    data = await resp.json()
    emb = data.get("_embedded", {})
    items = emb.get("contacts", [])
    return items[0]["id"] if items else None


async def create_lead(contact_id: int,
                      title: str,
                      pipeline_id: int) -> Optional[int]:
    """Создаёт сделку, привязывает контакт, возвращает id или None."""
    payload = [{
        "name": title,
        "pipeline_id": int(pipeline_id),
        "_embedded": {"contacts": [{"id": int(contact_id)}]},
    }]
    resp = await amo_request("POST", "/api/v4/leads", json_body=payload)
    txt = await resp.text()
    logging.info("📡 create_lead [%s]: %s", resp.status, txt)
    if resp.status != 200:
        return None
    data = await resp.json()
    emb = data.get("_embedded", {})
    items = emb.get("leads", [])
    return items[0]["id"] if items else None


async def add_text_note(lead_id: int, text: str) -> bool:
    """Добавляет текстовую заметку к сделке."""
    payload = [{
        "entity_id": int(lead_id),
        "note_type": "common",
        "params": {"text": text[:8000]},
    }]
    resp = await amo_request("POST", "/api/v4/leads/notes", json_body=payload)
    txt = await resp.text()
    logging.info("📎 add_text_note [%s]: %s", resp.status, txt)
    return 200 <= resp.status < 300


async def add_file_note(lead_id: int,
                        uuid: str,
                        file_name: str = "file.bin") -> bool:
    """Прикрепляет загруженный файл как заметку-attachment."""
    payload = [{
        "entity_id": int(lead_id),
        "note_type": "attachment",
        "params": {"attachments": [{"file_name": file_name, "uuid": uuid}]},
    }]
    resp = await amo_request("POST", "/api/v4/leads/notes", json_body=payload)
    txt = await resp.text()
    logging.info("📎 add_file_note [%s]: %s", resp.status, txt)
    return 200 <= resp.status < 300


async def create_lead_in_amo(chat_id: int, username: str) -> Optional[int]:
    """
    Высокоуровнево: создаёт контакт и сделку, кэширует lead_id в Redis.
    """
    name = username or f"Telegram {chat_id}"
    cid = await create_contact(name)
    if not cid:
        logging.warning("❌ create_contact failed")
        return None

    title = f"Новый запрос из Telegram ({name})"
    lid = await create_lead(cid, title, AMO_PIPELINE_ID)
    if not lid:
        logging.warning("❌ create_lead failed")
        return None

    await set_lead_id(chat_id, str(lid))
    logging.info("✅ lead %s created and cached for chat_id=%s", lid, chat_id)
    return lid


# ======================================================================
#                      Chat API v2 (origin/custom)
# ======================================================================

def _md5_hex_lower(data: bytes) -> str:
    """MD5(bytes) → hex lower."""
    return hashlib.md5(data).hexdigest().lower()


def _rfc1123_now_gmt() -> str:
    """Дата в RFC1123 ('GMT'), как требует amojo."""
    from email.utils import formatdate
    return formatdate(usegmt=True)


def _hmac_sha1_hex_ascii(src: str, secret_ascii: str) -> str:
    """
    HMAC-SHA1(src, key) → hex lower.

    Ключ трактуем как ASCII-строку (наш канал ожидает именно так).
    """
    key = secret_ascii.encode("utf-8")
    mac = hmac.new(key, src.encode("utf-8"), hashlib.sha1)
    return mac.hexdigest().lower()


async def send_chat_message_v2(scope_id: str,
                               chat_id: int,
                               text: str,
                               username: Optional[str] = None) -> bool:
    """
    Отправляет событие 'new_message' в Chat API.

    Требования валидатора:
    - conversation_id и user — на верхнем уровне объекта;
    - payload содержит message{type,text};
    - подпись: METHOD, MD5(hex), Content-Type, Date, PATH.
    """
    secret = AMO_CHAT_SECRET
    if not secret:
        logging.warning("⚠️ Chat v2: AMO_CHAT_SECRET empty")
        return False
    if not scope_id:
        logging.warning("⚠️ Chat v2: scope_id empty")
        return False

    body = {
        "event_type": "new_message",
        "conversation_id": f"tg_{chat_id}",
        "user": {
            "id": str(chat_id),
            "name": username or f"User {chat_id}",
        },
        "payload": {
            "message": {"type": "text", "text": (text or "")[:4000]}
        },
    }
    body_bytes = json.dumps(
        body, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    content_md5 = _md5_hex_lower(body_bytes)   # hex (важно)
    content_type = "application/json"
    date_gmt = _rfc1123_now_gmt()
    path = f"/v2/origin/custom/{scope_id}/chats"

    sign_src = "\n".join(
        ["POST", content_md5, content_type, date_gmt, path]
    )
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
