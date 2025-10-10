# amo_client.py
# 🔴 Подсистема интеграции с amoCRM:
# - автообновление токена по refresh_token
# - создание контактов и сделок
# - добавление примечаний к существующим сделкам

import os, aiohttp, asyncio, logging
from dotenv import load_dotenv
from pathlib import Path
import json

# загружаем .env (используем абсолютный путь)
ENV_PATH = "/var/www/medbot/.env"
load_dotenv(ENV_PATH)

AMO_API_URL = os.getenv("AMO_API_URL", "")
AMO_CLIENT_ID = os.getenv("AMO_CLIENT_ID", "")
AMO_CLIENT_SECRET = os.getenv("AMO_CLIENT_SECRET", "")
AMO_REDIRECT_URI = os.getenv("AMO_REDIRECT_URI", "")
AMO_REFRESH_TOKEN = os.getenv("AMO_REFRESH_TOKEN", "")
AMO_ACCESS_TOKEN = os.getenv("AMO_ACCESS_TOKEN", "")
AMO_PIPELINE_ID = os.getenv("AMO_PIPELINE_ID", "0")

# 🔴 автообновление токена
async def refresh_access_token() -> str:
    """Обновляет токен amoCRM через refresh_token и сохраняет в .env"""
    url = f"{AMO_API_URL}/oauth2/access_token"
    payload = {
        "client_id": AMO_CLIENT_ID,
        "client_secret": AMO_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": AMO_REFRESH_TOKEN,
        "redirect_uri": AMO_REDIRECT_URI,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Token refresh failed [{resp.status}]: {text}")
            data = await resp.json()
            new_token = data["access_token"]
            new_refresh = data.get("refresh_token", AMO_REFRESH_TOKEN)

            # 🔴 обновляем .env (перезаписываем токены)
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

            os.environ["AMO_ACCESS_TOKEN"] = new_token
            logging.info("✅ amoCRM token refreshed successfully")
            return new_token

# 🔴 создание сделки и контакта
async def create_lead_in_amo(chat_id: int, username: str) -> str | None:
    """Создаёт сделку и контакт в amoCRM, возвращает lead_id."""
    access_token = os.getenv("AMO_ACCESS_TOKEN")
    if not access_token:
        logging.warning("⚠️ No AMO_ACCESS_TOKEN in env")
        return None

    async with aiohttp.ClientSession() as s:
        # создаём контакт
        contact = {"name": username or f"Telegram {chat_id}"}
        async with s.post(
            f"{AMO_API_URL}/api/v4/contacts",
            headers={"Authorization": f"Bearer {access_token}"},
            json=[contact],
        ) as r:
            if r.status != 200:
                logging.warning(f"⚠️ Contact creation failed: {await r.text()}")
                return None
            res = await r.json()
            contact_id = res[0]["id"]

        # создаём сделку
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
            if r.status == 401:
                logging.warning("⚠️ Token expired during lead creation, refreshing...")
                await refresh_access_token()
                return await create_lead_in_amo(chat_id, username)
            if r.status != 200:
                logging.warning(f"❌ Lead creation failed [{r.status}]: {await r.text()}")
                return None
            data = await r.json()
            lead_id = data[0]["id"]
            logging.info(f"✅ Created amoCRM lead {lead_id} for chat_id={chat_id}")
            return lead_id
