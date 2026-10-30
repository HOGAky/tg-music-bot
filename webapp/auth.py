"""Авторизация Mini App: проверка подписи Telegram WebApp initData
(HMAC-SHA256, секрет = HMAC("WebAppData", bot_token)) + aiohttp-middleware
для всех /api/* кроме /api/stream/* (тег <audio> не шлёт кастомные заголовки)
и /api/batch/* (одноразовые подписанные ссылки)."""
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from aiohttp import web

from config import config

logger = logging.getLogger(__name__)

_PUBLIC_PREFIXES = ("/api/stream/", "/api/batch/")


def validate_init_data(init_data_raw: str, bot_token: str,
                       max_age: int = 86400) -> dict | None:
    """initData query-string -> dict пользователя или None при невалидной
    подписи/протухшем auth_date."""
    if not init_data_raw:
        return None
    try:
        pairs = dict(parse_qsl(init_data_raw, keep_blank_values=True))
    except Exception:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if time.time() - auth_date > max_age:
        return None

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(),
                          hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    try:
        return json.loads(pairs.get("user", "{}"))
    except Exception:
        return None


def extract_init_data(request: web.Request) -> str:
    """initData из заголовка X-Telegram-Init-Data, Bearer-токена
    или query-параметра (_data / initData) — для <audio> и window.open."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            init_data = auth[7:]
    if not init_data:
        init_data = request.query.get("_data") or request.query.get("initData", "")
    return init_data


@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path
    if not path.startswith("/api/"):
        return await handler(request)

    # даже на публичных роутах (stream) заселяем user_id, если initData валиден
    data = extract_init_data(request)
    user = validate_init_data(data, config.BOT_TOKEN) if data else None
    if user and "id" in user:
        request["user_id"] = int(user["id"])
        request["tg_user"] = user

    if path.startswith(_PUBLIC_PREFIXES):
        return await handler(request)
    if request.get("user_id") is None:
        return web.json_response({"error": "Unauthorized"}, status=401)
    return await handler(request)
