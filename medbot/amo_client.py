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
        async with session.post(url, json=payload, timeout=15) as resp:
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

async def create_lead_in_amo(chat_id: int, username: str) -> Optional[int]:
    """
    🔴 Создаёт контакт и сделку в amoCRM, если их ещё нет.
    Возвращает ID сделки (lead_id) или None при ошибке.
    """
    access_token = os.getenv("AMO_ACCESS_TOKEN")
    if not access_token:
        logging.warning("⚠️ No AMO_ACCESS_TOKEN in environment")
        return None

    # если сделка уже есть — не дублируем
    existing_lead = get_lead_id(chat_id)
    if existing_lead:
        logging.info(f"♻️ Lead already exists for chat_id={chat_id}: {existing_lead}")
        return int(existing_lead)

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            # --- создаём контакт ---
            contact_payload = {"name": username or f"Telegram {chat_id}"}
            async with s.post(
                f"{AMO_API_URL}/api/v4/contacts",
                headers=headers,
                json=[contact_payload],
            ) as contact_resp:
                if contact_resp.status == 401:
                    # токен устарел → обновляем и повторяем
                    logging.warning("⚠️ Token expired during contact creation, refreshing...")
                    await refresh_access_token()
                    return await create_lead_in_amo(chat_id, username)

                if contact_resp.status != 200:
                    err = await contact_resp.text()
                    logging.warning(f"⚠️ Contact creation failed [{contact_resp.status}]: {err}")
                    return None

                contact_data = await contact_resp.json()
                contact_id = contact_data[0]["id"]

            # --- создаём сделку ---
            lead_payload = {
                "name": f"Новый запрос из Telegram ({username})",
                "pipeline_id": int(AMO_PIPELINE_ID),
                "_embedded": {"contacts": [{"id": contact_id}]},
            }

            async with s.post(
                f"{AMO_API_URL}/api/v4/leads",
                headers=headers,
                json=[lead_payload],
            ) as lead_resp:
                if lead_resp.status == 401:
                    logging.warning("⚠️ Token expired during lead creation, refreshing...")
                    await refresh_access_token()
                    return await create_lead_in_amo(chat_id, username)

                if lead_resp.status != 200:
                    err = await lead_resp.text()
                    logging.warning(f"❌ Lead creation failed [{lead_resp.status}]: {err}")
                    return None

                lead_data = await lead_resp.json()
                lead_id = lead_data[0]["id"]

                # 🔴 сохраняем связь chat_id → lead_id в Redis
                set_lead_id(chat_id, lead_id)

                logging.info(f"✅ Created amoCRM lead {lead_id} for chat_id={chat_id}")
                return lead_id

    except aiohttp.ClientError as e:
        logging.warning(f"⚠️ Network error in create_lead_in_amo: {e}")
        return None
    except Exception as e:
        logging.warning(f"⚠️ Exception in create_lead_in_amo: {e}")
        return None
