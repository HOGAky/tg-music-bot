import hashlib
import logging
import os
import time
from collections.abc import Callable
from typing import Any

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

from config import config

logger = logging.getLogger(__name__)

# быстрые таймауты: недоступный S3 не должен тормозить отправку трека
_BOTO_CONFIG = BotoConfig(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2})
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_RETRYABLE_ERROR_CODES = {
    "InternalError",
    "RequestTimeout",
    "RequestTimeoutException",
    "SlowDown",
    "Throttling",
}

# ID нашего lifecycle-правила: чужие правила бакета при установке не трогаем
_LIFECYCLE_RULE_ID = "musicbot-cache-rotation"
_MISSING_OBJECT_CODES = {"404", "NoSuchKey", "NotFound"}
_NO_LIFECYCLE_CODES = {"NoSuchLifecycleConfiguration", "NotFound"}


class S3Storage:
    """Кэш mp3 в S3-совместимом хранилище (Primary) + гео-резерв (Secondary).

    Все операции отказоустойчивые: при недоступности Primary выполняются на
    Secondary (если его ключи заданы), при недоступности обоих возвращают
    None/False с записью в лог — бот продолжает работать на локальном кэше
    (Graceful Degradation по ТЗ v3.0)."""

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return bool(
            config.USE_S3_STORAGE
            and config.S3_ENDPOINT_URL
            and config.S3_BUCKET_NAME
            and config.S3_ACCESS_KEY_ID
            and config.S3_SECRET_ACCESS_KEY
        )

    @property
    def secondary_enabled(self) -> bool:
        """Резервный S3 настроен (все 4 параметра Secondary заданы)."""
        return bool(
            config.S3_SECONDARY_ENDPOINT_URL
            and config.S3_SECONDARY_BUCKET_NAME
            and config.S3_SECONDARY_ACCESS_KEY_ID
            and config.S3_SECONDARY_SECRET_ACCESS_KEY
        )

    @staticmethod
    def key_for(url: str, quality: int) -> str:
        """Детерминированный ключ кэша: одинаковый URL+качество -> один файл."""
        digest = hashlib.sha256(f"{url}#{quality}".encode()).hexdigest()[:24]
        return f"{digest}_{quality}.mp3"

    @staticmethod
    def _make_client(endpoint_url: str, access_key: str, secret_key: str):
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=config.S3_REGION,
            config=_BOTO_CONFIG,
        )

    def _get_client(self, target: str = "primary"):
        if target not in self._clients:
            if target == "secondary":
                self._clients[target] = self._make_client(
                    config.S3_SECONDARY_ENDPOINT_URL,
                    config.S3_SECONDARY_ACCESS_KEY_ID,
                    config.S3_SECONDARY_SECRET_ACCESS_KEY,
                )
            else:
                self._clients[target] = self._make_client(
                    config.S3_ENDPOINT_URL,
                    config.S3_ACCESS_KEY_ID,
                    config.S3_SECRET_ACCESS_KEY,
                )
        return self._clients[target]

    @staticmethod
    def _bucket_for(target: str) -> str:
        if target == "secondary":
            return config.S3_SECONDARY_BUCKET_NAME
        return config.S3_BUCKET_NAME

    def _targets(self) -> list[str]:
        """Primary всегда, Secondary — только при заданных ключах (резерв)."""
        if self.secondary_enabled:
            return ["primary", "secondary"]
        return ["primary"]

    def _full_key(self, key: str) -> str:
        prefix = (config.S3_CACHE_PREFIX or "").strip("/")
        clean_key = key.lstrip("/")
        if not prefix:
            return clean_key
        return f"{prefix}/{clean_key}"

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, EndpointConnectionError):
            return True
        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {})
            if error.get("Code") in _RETRYABLE_ERROR_CODES:
                return True
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            return status in _RETRYABLE_STATUS_CODES
        return isinstance(exc, BotoCoreError)

    def _call_with_retry(self, action: str, key: str, operation: Callable):
        last_error = None
        for attempt in range(1, 4):
            try:
                return operation()
            except (BotoCoreError, ClientError, OSError) as exc:
                last_error = exc
                if attempt == 3 or not self._is_retryable(exc):
                    break
                delay = 0.5 * attempt
                logger.warning(
                    "S3 %s временно не удался (%s), попытка %s/3: %s",
                    action,
                    key,
                    attempt,
                    exc,
                )
                time.sleep(delay)
        raise last_error

    def _run(self, action: str, key: str, operation: Callable[[str], Any]) -> tuple[bool, Any]:
        """Пробует Primary; при ошибках связи/5xx переключается на Secondary.

        operation(target) -> результат; исключение означает провал таргета.
        (ok, value): ok=False — все таргеты недоступны, value=None."""
        errors: list[str] = []
        for target in self._targets():
            try:
                value = self._call_with_retry(
                    action, key, lambda target=target: operation(target)
                )
                if target != "primary":
                    logger.info("S3 %s выполнен на резервном хранилище (%s)", action, target)
                return True, value
            except (BotoCoreError, ClientError, OSError) as exc:
                errors.append(f"{target}: {exc}")
                logger.warning(
                    "S3 %s не удался на таргете %s (%s): %s",
                    action, target, key, exc,
                )
        logger.error(
            "S3 %s недоступен полностью [%s] — фолбэк на локальный диск: %s",
            key, action, "; ".join(errors),
        )
        return False, None

    def upload_file(self, file_path: str, key: str) -> str | None:
        """Загружает файл в Primary (при сбое — в Secondary), возвращает ключ или None."""
        if not self.enabled:
            return None
        ok, _ = self._run(
            "upload",
            key,
            lambda target: self._get_client(target).upload_file(
                file_path,
                self._bucket_for(target),
                self._full_key(key),
            ),
        )
        if not ok:
            logger.warning("S3 upload не удался (%s) — остаёмся на локальном кэше", key)
            return None
        return key

    def download_file(self, key: str, local_path: str) -> str | None:
        """Скачивает файл из Primary/Secondary в local_path, возвращает путь или None."""
        if not self.enabled:
            return None
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        ok, _ = self._run(
            "download",
            key,
            lambda target: self._get_client(target).download_file(
                self._bucket_for(target),
                self._full_key(key),
                local_path,
            ),
        )
        return local_path if ok else None

    def file_exists(self, key: str) -> bool:
        """True, если файл есть в Primary или Secondary; False при отсутствии/сбое."""
        if not self.enabled:
            return False
        for target in self._targets():
            try:
                self._call_with_retry(
                    "head_object",
                    key,
                    lambda target=target: self._get_client(target).head_object(
                        Bucket=self._bucket_for(target),
                        Key=self._full_key(key),
                    ),
                )
                return True
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if str(code) in _MISSING_OBJECT_CODES:
                    if target == "primary" and self.secondary_enabled:
                        continue  # объекта нет в Primary — ищем в резервном
                    return False
                logger.warning("S3 head_object не удался (%s): %s — считаем, что файла нет", key, e)
            except BotoCoreError as e:
                logger.warning("S3 недоступен (%s): %s", key, e)
        return False

    def delete_file(self, key: str) -> None:
        if not self.enabled:
            return
        self._run(
            "delete",
            key,
            lambda target: self._get_client(target).delete_object(
                Bucket=self._bucket_for(target),
                Key=self._full_key(key),
            ),
        )

    def get_public_url(self, key: str, ttl: int = 3600) -> str | None:
        """Временная (pre-signed) ссылка на файл в бакете (подпись считается локально)."""
        if not self.enabled:
            return None
        try:
            return self._get_client("primary").generate_presigned_url(
                "get_object",
                Params={"Bucket": config.S3_BUCKET_NAME, "Key": self._full_key(key)},
                ExpiresIn=ttl or config.S3_URL_TTL,
            )
        except (BotoCoreError, ClientError) as e:
            logger.warning("S3 presigned url не удался (%s): %s", key, e)
            return None

    def setup_lifecycle_policy(self) -> bool:
        """Ставит Lifecycle Rule: объекты кэша удаляются через CACHE_MAX_AGE_DAYS.

        Вызывается при старте бота (в отдельном потоке). Правило мержится с уже
        существующими правилами бакета — чужие правила не затираются. Любые ошибки
        только логируются: недоступность S3 не должна ронять запуск бота."""
        if not self.enabled:
            return False
        days = config.CACHE_MAX_AGE_DAYS
        if days < 1:
            logger.warning("CACHE_MAX_AGE_DAYS=%s — lifecycle-правило не применяется", days)
            return False

        rule = {
            "ID": _LIFECYCLE_RULE_ID,
            "Status": "Enabled",
            "Filter": {"Prefix": config.S3_CACHE_PREFIX or ""},
            "Expiration": {"Days": days},
        }
        ok = True
        for target in self._targets():
            client = self._get_client(target)
            bucket = self._bucket_for(target)
            rules: list[dict] = []
            try:
                resp = client.get_bucket_lifecycle_configuration(Bucket=bucket)
                rules = [r for r in resp.get("Rules", []) if r.get("ID") != _LIFECYCLE_RULE_ID]
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if str(code) not in _NO_LIFECYCLE_CODES:
                    logger.warning(
                        "S3: не удалось прочитать lifecycle бакета %s (%s) — правило "
                        "не применяем, чтобы не затереть чужие: %s",
                        bucket, target, exc,
                    )
                    ok = False
                    continue
            except BotoCoreError as exc:
                logger.warning(
                    "S3 недоступен (%s): lifecycle для %s отложен: %s",
                    target, bucket, exc,
                )
                ok = False
                continue

            rules.append(rule)
            try:
                client.put_bucket_lifecycle_configuration(
                    Bucket=bucket,
                    LifecycleConfiguration={"Rules": rules},
                )
                logger.info(
                    "S3 lifecycle установлен: объекты %s удаляются через %s дн. "
                    "(бакет %s, таргет %s)",
                    config.S3_CACHE_PREFIX, days, bucket, target,
                )
            except (BotoCoreError, ClientError) as exc:
                logger.warning("S3: не удалось применить lifecycle для %s: %s", bucket, exc)
                ok = False
        return ok


storage = S3Storage()
