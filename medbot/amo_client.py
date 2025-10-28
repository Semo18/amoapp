"""
amo_client.py — Работа с amoCRM API
-----------------------------------
Теперь с:
  • Автообновлением access/refresh токенов.
  • Безопасной записью в .env и JSON-кэш.
  • Redis-lock при одновременных запросах.
  • Автоматическим retry при 401 Unauthorized.
"""

import os
import json
import time
import aiohttp
import asyncio
import hashlib
import hmac
import binascii
import logging
from typing import Optional
from storage import redis  # 🔴 подключаем существующий Redis-клиент

# ---------------------------------------------------------------------
# Константы и пути
# ---------------------------------------------------------------------

AMO_RUNTIME_DIR = "/var/www/medbot/runtime"
AMO_TOKEN_CACHE = os.path.join(AMO_RUNTIME_DIR, "amo_tokens.json")
AMO_REQUEST_TIMEOUT_SEC = 15

# ---------------------------------------------------------------------
# Утилиты подписи (для Chat API)
# ---------------------------------------------------------------------


def _md5_hex_lower(data: bytes) -> str:
    """Возвращает MD5-хеш в нижнем регистре (hex)."""
    return hashlib.md5(data).hexdigest().lower()


def _rfc1123_now_gmt() -> str:
    """Возвращает текущую дату в формате RFC1123 (GMT)."""
    return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())


def _hmac_sha1_hex(src: str, secret: str) -> str:
    """
    Возвращает HMAC-SHA1(src, key) в hex lower.
    Если secret похож на hex, трактуется как байты.
    """
    is_hex = len(secret) in (40, 64) and all(
        c in "0123456789abcdef" for c in secret.lower()
    )
    key = binascii.unhexlify(secret) if is_hex else secret.encode()
    return hmac.new(key, src.encode(), hashlib.sha1).hexdigest().lower()


# ---------------------------------------------------------------------
# AmoTokenManager — универсальный менеджер токенов
# ---------------------------------------------------------------------


class AmoTokenManager:
    """Обеспечивает безопасное обновление и кэширование amoCRM токенов."""

    def __init__(self) -> None:
        self.domain = os.getenv("AMO_API_URL", "").replace("https://", "")
        self.client_id = os.getenv("AMO_CLIENT_ID")
        self.client_secret = os.getenv("AMO_CLIENT_SECRET")
        self.redirect_uri = os.getenv("AMO_REDIRECT_URI")
        self._env_path = "/var/www/medbot/.env"
        self._lock_key = "amo:token:lock"
        self._access_token = os.getenv("AMO_ACCESS_TOKEN")
        self._refresh_token = os.getenv("AMO_REFRESH_TOKEN")

    # --------------------------
    # 🔹 Основные методы
    # --------------------------

    async def get_access_token(self) -> str:
        """
        Возвращает действующий access_token.
        Проверяет Redis-кэш → JSON → env, обновляет при необходимости.
        """
        cached = await redis.get("amo:access_token")
        if cached:
            return cached.decode()

        if not self._access_token or not await self._validate_token():
            await self.refresh_tokens()

        await redis.setex("amo:access_token", 3600, self._access_token)
        return self._access_token

    async def refresh_tokens(self) -> None:
        """Обновляет access/refresh токены с блокировкой Redis."""
        async with redis.lock(self._lock_key, timeout=30):
            logging.info("♻️ Refreshing amoCRM tokens...")
            url = f"https://{self.domain}/oauth2/access_token"
            payload = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "redirect_uri": self.redirect_uri,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=AMO_REQUEST_TIMEOUT_SEC
                ) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        raise RuntimeError(
                            f"Token refresh failed [{resp.status}]: {data}"
                        )

            self._access_token = data["access_token"]
            self._refresh_token = data["refresh_token"]
            await self._persist_tokens(data)
            logging.info("✅ amoCRM tokens updated successfully")

    async def _persist_tokens(self, data: dict) -> None:
        """Сохраняет токены в JSON и .env."""
        os.makedirs(AMO_RUNTIME_DIR, exist_ok=True)
        json.dump(data, open(AMO_TOKEN_CACHE, "w"), indent=2)

        # обновляем .env построчно
        lines = []
        with open(self._env_path, "r") as f:
            for line in f:
                if line.startswith("AMO_ACCESS_TOKEN="):
                    line = f"AMO_ACCESS_TOKEN={data['access_token']}\n"
                elif line.startswith("AMO_REFRESH_TOKEN="):
                    line = f"AMO_REFRESH_TOKEN={data['refresh_token']}\n"
                lines.append(line)
        with open(self._env_path, "w") as f:
            f.writelines(lines)

    async def _validate_token(self) -> bool:
        """Проверяет, жив ли текущий токен (GET /account)."""
        try:
            url = f"https://{self.domain}/api/v4/account"
            headers = {"Authorization": f"Bearer {self._access_token}"}
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=headers) as r:
                    return r.status == 200
        except Exception:
            return False


# ---------------------------------------------------------------------
# Универсальный amo_request — безопасный запрос с auto-refresh
# ---------------------------------------------------------------------


async def amo_request(
    method: str,
    path: str,
    json_data: Optional[dict] = None,
    retries: int = 2,
) -> Optional[dict]:
    """Делает запрос в amoCRM с автоматическим обновлением токена."""
    manager = AmoTokenManager()
    token = await manager.get_access_token()
    domain = manager.domain
    url = f"https://{domain}{path}"

    for attempt in range(retries):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, json=json_data, timeout=AMO_REQUEST_TIMEOUT_SEC
            ) as resp:
                text = await resp.text()
                if resp.status == 401 and attempt == 0:
                    logging.warning("⚠️ Token expired, refreshing...")
                    await manager.refresh_tokens()
                    token = await manager.get_access_token()
                    continue
                if 200 <= resp.status < 300:
                    return await resp.json()
                logging.error(f"❌ amo_request [{resp.status}]: {text}")
                return None
    return None


# ---------------------------------------------------------------------
# ChatAPI v2 — отправка сообщений в amoCRM
# ---------------------------------------------------------------------


async def send_chat_message_v2(
    scope_id: str,
    chat_id: int,
    text: str,
    username: Optional[str] = None,
) -> bool:
    """Отправляет событие new_message в Chat API (amojo)."""
    secret = os.getenv("AMO_CHAT_SECRET", "")
    if not secret or not scope_id:
        logging.warning("⚠️ Chat v2 missing credentials")
        return False

    payload = {
        "event_type": "new_message",
        "payload": {
            "conversation_id": f"tg_{chat_id}",
            "message": {"type": "text", "text": text[:4000]},
            "user": {"id": str(chat_id), "name": username or f"User {chat_id}"},
        },
    }
    body = json.dumps(payload, ensure_ascii=False).encode()
    content_type = "application/json"
    content_md5 = _md5_hex_lower(body)
    date_gmt = _rfc1123_now_gmt()
    path = f"/v2/origin/custom/{scope_id}/chats"
    sign_str = "\n".join(["POST", content_md5, content_type, date_gmt, path])
    signature = _hmac_sha1_hex(sign_str, secret)
    url = f"https://amojo.amocrm.ru{path}"

    async with aiohttp.ClientSession() as s:
        async with s.post(
            url,
            data=body,
            headers={
                "Content-Type": content_type,
                "Content-MD5": content_md5,
                "Date": date_gmt,
                "X-Signature": signature,
            },
            timeout=AMO_REQUEST_TIMEOUT_SEC,
        ) as r:
            txt = await r.text()
            logging.info(f"💬 ChatAPI v2 send [{r.status}]: {txt}")
            return 200 <= r.status < 300


# ---------------------------------------------------------------------
# Стартовая инициализация
# ---------------------------------------------------------------------


async def _startup() -> None:
    """Проверяет токен при запуске и обновляет при необходимости."""
    try:
        manager = AmoTokenManager()
        ok = await manager._validate_token()
        if not ok:
            logging.info("🔄 Refreshing amoCRM tokens on startup...")
            await manager.refresh_tokens()
        else:
            logging.info("✅ amoCRM token is valid on startup")
    except Exception as e:
        logging.warning(f"⚠️ amoCRM startup check failed: {e}")

# ---------------------------------------------------------------------
# Совместимость со старым кодом (app.py ожидает refresh_access_token)
# ---------------------------------------------------------------------


async def refresh_access_token() -> str:
    """
    Обёртка для совместимости.
    Вызывает AmoTokenManager.refresh_tokens() и возвращает новый access_token.
    """
    from amo_client import AmoTokenManager  # локальный импорт, чтобы избежать рекурсии
    manager = AmoTokenManager()
    await manager.refresh_tokens()
    return manager._access_token
