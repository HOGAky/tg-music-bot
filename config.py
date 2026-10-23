from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    BOT_TOKEN: str
    SPOTIFY_CLIENT_ID: str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    YANDEX_TOKEN: str = ""  # yandex-music token: приватные плейлисты; публичные работают без него
    DB_URL: str = "sqlite+aiosqlite:///data/musicbot.db"
    CACHE_DIR: str = "downloads"
    MAX_CONCURRENT_DOWNLOADS: int = 4
    MAX_CONCURRENT_UPDATES: int = Field(default=64, ge=1, le=1000)
    DB_POOL_SIZE: int = Field(default=20, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0)
    DB_POOL_TIMEOUT: int = Field(default=30, ge=1)

    # ---- Ротация mp3-кэша ----
    CACHE_MAX_AGE_DAYS: int = 30       # файлы старше этого срока удаляются безусловно
    CACHE_MAX_SIZE_BYTES: int = 1_500_000_000  # после возрастной чистки — LRU-порог (1.5 ГБ)

    # ---- Mini App ----
    WEBAPP_URL: str = ""        # публичный https-адрес вида https://домен/app/
    API_ENABLED: bool = False   # поднимать ли HTTP-сервер веб-плеера
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8080

    # ---- Webhook ----
    WEBHOOK_ENABLED: bool = False   # использовать webhook вместо polling
    WEBHOOK_URL: str = ""           # публичный https-адрес бота (e.g., https://bot.example.com)
    WEBHOOK_PATH: str = "/webhook"  # путь для webhook endpoint
    WEBHOOK_SECRET: str = ""        # секрет для валидации webhook (опционально)

    # ---- Logging ----
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"  # text or json
    MONITOR_BOT_TOKEN: str = ""
    MONITOR_CHAT_ID: str = ""
    MONITOR_INTERVAL_SECONDS: int = Field(default=30, ge=5)

    # ---- S3 (Yandex Object Storage) ----
    USE_S3_STORAGE: bool = False             # кэшировать mp3 в S3
    S3_ENDPOINT_URL: str = ""                # e.g., https://storage.yandexcloud.net
    S3_BUCKET_NAME: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_REGION: str = "ru-central1"
    S3_CACHE_PREFIX: str = "musicbot/"
    S3_URL_TTL: int = 3600

    # ---- Вторичный S3 (гео-резервирование: альтернативный регион/провайдер) ----
    # Если ключи заданы, при недоступности Primary S3 операции автоматически
    # переключаются на Secondary (Graceful Fallback).
    S3_SECONDARY_ENDPOINT_URL: str = ""
    S3_SECONDARY_BUCKET_NAME: str = ""
    S3_SECONDARY_ACCESS_KEY_ID: str = ""
    S3_SECONDARY_SECRET_ACCESS_KEY: str = ""

    # публичный client_id веб-плеера SoundCloud (api-v2); обновляется
    # переизвлечением со страницы soundcloud.com, если однажды протухнет
    SOUNDCLOUD_CLIENT_ID: str = "tALhrIHY34p48SpHQCwLnMs3ivsiyNZI"

    # ---- Redis (FSM + кэш, мультиинстансность) ----
    REDIS_URL: str = ""                      # e.g., redis://localhost:6379/0
    USE_REDIS: bool = False


config = Config()
