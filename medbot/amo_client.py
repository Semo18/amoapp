import os
import json
import logging
import aiohttp
from dotenv import load_dotenv

# Загружаем .env (для локального запуска и systemd)
load_dotenv()

AMO_API_URL = os.getenv("AMO_API_URL")
AMO_CLIENT_ID = os.getenv("AMO_CLIENT_ID")
AMO_CLIENT_SECRET = os.getenv("AMO_CLIENT_SECRET")
AMO_REDIRECT_URI = os.getenv("AMO_REDIRECT_URI")
AMO_REFRESH_TOKEN = os.getenv("AMO_REFRESH_TOKEN")
AMO_ACCESS_TOKEN = os.getenv("AMO_ACCESS_TOKEN")
ENV_PATH = "/var/www/medbot/.env"  # путь к .env

# 🔹 вспомогательная функция для обновления токена
async def refresh_access_token() -> str:
    """Обновляет ACCESS_TOKEN через refresh_token"""
    logging.info("♻️  Refreshing amoCRM access token...")
    url = f"{AMO_API_URL}/oauth2/access_token"
    data = {
        "client_id": AMO_CLIENT_ID,
        "client_secret": AMO_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": AMO_REFRESH_TOKEN,
        "redirect_uri": AMO_REDIRECT_URI,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data) as resp:
            if resp.status != 200:
                text = await resp.text()
                logging.error(f"❌ Failed to refresh token ({resp.status}): {text}")
                raise RuntimeError(f"Failed to refresh amoCRM token: {resp.status}")
            result = await resp.json()

    new_access = result["access_token"]
    new_refresh = result["refresh_token"]

    # 🔸 Перезаписываем в .env
    _update_env_file("AMO_ACCESS_TOKEN", new_access)
    _update_env_file("AMO_REFRESH_TOKEN", new_refresh)

    # Обновляем переменные окружения текущего процесса
    os.environ["AMO_ACCESS_TOKEN"] = new_access
    os.environ["AMO_REFRESH_TOKEN"] = new_refresh

    logging.info("✅ amoCRM token refreshed successfully")
    return new_access


def _update_env_file(key: str, value: str) -> None:
    """Перезаписывает значение переменной в .env"""
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

        found = False
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            for line in lines:
                if line.startswith(f"{key}="):
                    f.write(f"{key}={value}\n")
                    found = True
                else:
                    f.write(line)
            if not found:
                f.write(f"{key}={value}\n")
    except Exception as e:
        logging.error(f"Ошибка при обновлении .env: {e}")
