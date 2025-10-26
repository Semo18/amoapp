#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os  # работа с переменными окружения
import hmac  # HMAC подпись
import hashlib  # md5 и sha1
import datetime  # заголовок Date в GMT
import json  # сериализация тела запросов
import sys  # выход с кодом ошибки
import requests  # HTTP-клиент (pip install requests)


def _must_env(name: str) -> str:
    """Достаём обязательную переменную окружения или падаем с понятной ошибкой."""
    val = os.getenv(name)  # читаем переменную
    if not val:  # если пусто
        print(f"❌ env {name} is not set", file=sys.stderr)  # лог ошибки
        sys.exit(1)  # выходим с кодом 1
    return val  # отдаём значение


def get_amojo_id(amo_domain: str, token: str) -> str:
    """Получаем amojo_id аккаунта через /api/v4/account?with=amojo_id."""
    url = f"https://{amo_domain}/api/v4/account"  # базовый URL
    params = {"with": "amojo_id"}  # запрашиваем доп. поле
    headers = {"Authorization": f"Bearer {token}"}  # авторизация OAuth
    resp = requests.get(url, params=params, headers=headers, timeout=15)  # GET
    if resp.status_code // 100 != 2:  # проверяем 2xx
        print(f"❌ account GET [{resp.status_code}]: {resp.text}", file=sys.stderr)
        sys.exit(1)  # выходим при ошибке
    data = resp.json()  # парсим JSON
    amojo_id = data.get("amojo_id")  # берём amojo_id
    if not amojo_id:  # если нет
        print("❌ amojo_id not found in response", file=sys.stderr)  # лог
        sys.exit(1)  # выходим
    return amojo_id  # отдаём amojo_id


def build_signature(method: str, path: str, body: str, secret: str) -> tuple[str, str, str, str]:
    """
    Готовим заголовки Date, Content-Type, Content-MD5, X-Signature по правилам Chat API.
    Возвращаем (date_gmt, content_type, md5_hex, signature_hex).
    """
    method_up = method.upper()  # метод заглавными
    content_type = "application/json"  # тип тела
    date_gmt = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")  # RFC 1123
    md5_hex = hashlib.md5(body.encode("utf-8")).hexdigest()  # md5 от тела
    sign_str = "\n".join([method_up, md5_hex, content_type, date_gmt, path])  # строка для подписи
    signature_hex = hmac.new(  # считаем HMAC-SHA1
        secret.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha1
    ).hexdigest()
    return date_gmt, content_type, md5_hex, signature_hex  # отдаём заголовки


def connect_channel(chat_channel_id: str, secret: str, amojo_id: str) -> str:
    """
    Делаем POST /v2/origin/custom/{channel_id}/connect к amojo.amocrm.ru.
    Возвращаем scope_id при успехе.
    """
    path = f"/v2/origin/custom/{chat_channel_id}/connect"  # путь без домена/параметров
    url = f"https://amojo.amocrm.ru{path}"  # полный URL Chat API
    body_obj = {  # тело запроса
        "account_id": amojo_id,  # amojo_id аккаунта
        "title": "MedBot Bridge",  # отображаемое имя канала
        "hook_api_version": "v2",  # версия хук-протокола
    }
    body = json.dumps(body_obj, separators=(",", ":"))  # компактный JSON
    date_gmt, content_type, md5_hex, signature_hex = build_signature(  # собираем подпись
        "POST", path, body, secret
    )
    headers = {  # заголовки по требованиям Chat API
        "Date": date_gmt,  # время запроса
        "Content-Type": content_type,  # тип тела
        "Content-MD5": md5_hex,  # md5 от тела
        "X-Signature": signature_hex,  # HMAC-SHA1 подпись
    }
    resp = requests.post(url, data=body, headers=headers, timeout=15)  # выполняем POST
    if resp.status_code // 100 != 2:  # проверяем 2xx
        print(f"❌ connect [{resp.status_code}]: {resp.text}", file=sys.stderr)  # лог ошибки
        sys.exit(1)  # выходим
    data = resp.json()  # парсим JSON
    scope_id = data.get("scope_id") or data.get("scope")  # читаем scope_id
    if not scope_id:  # если нет
        print(f"❌ scope_id not found in response: {data}", file=sys.stderr)  # лог
        sys.exit(1)  # выходим
    return scope_id  # отдаём scope_id


def main() -> None:
    """Главная точка входа: получаем amojo_id → делаем connect → печатаем scope_id."""
    amo_domain = _must_env("AMO_DOMAIN")  # домен аккаунта
    access_token = _must_env("AMO_ACCESS_TOKEN")  # OAuth токен
    chat_channel_id = _must_env("CHAT_CHANNEL_ID")  # ID канала (id из письма)
    chat_secret = _must_env("CHAT_SECRET")  # секрет канала (secret_key из письма)

    print("🔎 Getting amojo_id ...")  # лог процесса
    amojo_id = get_amojo_id(amo_domain, access_token)  # получаем amojo_id
    print(f"✅ amojo_id: {amojo_id}")  # выводим amojo_id

    print("🔗 Connecting account to chat channel ...")  # лог
    scope_id = connect_channel(chat_channel_id, chat_secret, amojo_id)  # connect → scope_id
    print(f"✅ scope_id: {scope_id}")  # печатаем scope_id
    print("\n➡️  Подставьте его в ваш webhook:")  # подсказка
    print(f"    https://amo.ap-development.com/medbot/amo-webhook/{scope_id}")  # пример URL


if __name__ == "__main__":  # точка запуска
    main()  # вызываем main
