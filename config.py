from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    BOT_TOKEN: str
    SPOTIFY_CLIENT_ID: str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    DB_URL: str = "sqlite+aiosqlite:///data/musicbot.db"
    CACHE_DIR: str = "downloads"
    MAX_CONCURRENT_DOWNLOADS: int = 4

    # ---- Mini App ----
    WEBAPP_URL: str = ""        # публичный https-адрес вида https://домен/app/
    API_ENABLED: bool = False   # поднимать ли HTTP-сервер веб-плеера
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8080


config = Config()