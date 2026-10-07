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

    # ---- S3 (Yandex Object Storage) ----
    USE_S3_STORAGE: bool = False             # кэшировать mp3 в S3
    S3_ENDPOINT_URL: str = ""                # e.g., https://storage.yandexcloud.net
    S3_BUCKET_NAME: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_REGION: str = "ru-central1"
    S3_CACHE_PREFIX: str = "musicbot/"

    # публичный client_id веб-плеера SoundCloud (api-v2); обновляется
    # переизвлечением со страницы soundcloud.com, если однажды протухнет
    SOUNDCLOUD_CLIENT_ID: str = "tALhrIHY34p48SpHQCwLnMs3ivsiyNZI"

    # ---- Redis (FSM + кэш, мультиинстансность) ----
    REDIS_URL: str = ""                      # e.g., redis://localhost:6379/0
    USE_REDIS: bool = False


config = Config()