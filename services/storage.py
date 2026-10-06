import hashlib
import logging

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from config import config

logger = logging.getLogger(__name__)

# быстрые таймауты: недоступный S3 не должен тормозить отправку трека
_BOTO_CONFIG = BotoConfig(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2})


class S3Storage:
    """Кэш mp3 в Yandex Object Storage (S3-совместимом).

    Все операции отказоустойчивые: при любой ошибке возвращают None/False
    и пишут warning — бот продолжает работать на локальном кэше."""

    def __init__(self) -> None:
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(config.USE_S3_STORAGE and config.S3_BUCKET_NAME)

    @staticmethod
    def key_for(url: str, quality: int) -> str:
        """Детерминированный ключ кэша: одинаковый URL+качество -> один файл."""
        digest = hashlib.sha256(f"{url}#{quality}".encode()).hexdigest()[:24]
        return f"{digest}_{quality}.mp3"

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=config.S3_ENDPOINT_URL or None,
                aws_access_key_id=config.S3_ACCESS_KEY_ID or None,
                aws_secret_access_key=config.S3_SECRET_ACCESS_KEY or None,
                region_name=config.S3_REGION,
                config=_BOTO_CONFIG,
            )
        return self._client

    def _full_key(self, key: str) -> str:
        return f"{config.S3_CACHE_PREFIX}{key}"

    def upload_file(self, file_path: str, key: str) -> str | None:
        """Загружает файл в S3, возвращает ключ или None при ошибке."""
        if not self.enabled:
            return None
        try:
            self._get_client().upload_file(file_path, config.S3_BUCKET_NAME, self._full_key(key))
            return key
        except (BotoCoreError, ClientError, OSError) as e:
            logger.warning("S3 upload не удался (%s): %s — остаёмся на локальном кэше", key, e)
            return None

    def download_file(self, key: str, local_path: str) -> str | None:
        """Скачивает файл из S3 в local_path, возвращает путь или None."""
        if not self.enabled:
            return None
        try:
            self._get_client().download_file(
                config.S3_BUCKET_NAME, self._full_key(key), local_path)
            return local_path
        except (BotoCoreError, ClientError, OSError) as e:
            logger.warning("S3 download не удался (%s): %s", key, e)
            return None

    def file_exists(self, key: str) -> bool:
        if not self.enabled:
            return False
        try:
            self._get_client().head_object(Bucket=config.S3_BUCKET_NAME, Key=self._full_key(key))
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            logger.warning("S3 head_object не удался (%s): %s — считаем, что файла нет", key, e)
            return False
        except BotoCoreError as e:
            logger.warning("S3 недоступен (%s): %s", key, e)
            return False

    def delete_file(self, key: str) -> None:
        if not self.enabled:
            return
        try:
            self._get_client().delete_object(Bucket=config.S3_BUCKET_NAME, Key=self._full_key(key))
        except (BotoCoreError, ClientError, OSError) as e:
            logger.warning("S3 delete не удался (%s): %s", key, e)

    def get_public_url(self, key: str, ttl: int = 3600) -> str | None:
        """Временная (pre-signed) ссылка на файл в бакете."""
        if not self.enabled:
            return None
        try:
            return self._get_client().generate_presigned_url(
                "get_object",
                Params={"Bucket": config.S3_BUCKET_NAME, "Key": self._full_key(key)},
                ExpiresIn=ttl,
            )
        except (BotoCoreError, ClientError) as e:
            logger.warning("S3 presigned url не удался (%s): %s", key, e)
            return None


storage = S3Storage()
