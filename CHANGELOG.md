# Changelog

Все изменения проекта для деплоя на Yandex Cloud.

## [v1.2.0] - 2026-10-13

### Этап 1 — Автоочистка кэша (30 дней) и S3 multi-storage fallback

#### Конфигурация
- `CACHE_MAX_AGE_DAYS` (30) и `CACHE_MAX_SIZE_BYTES` (LRU-порог 1.5 ГБ) в `config.py`/`.env`
- Настройки вторичного S3-бакета: `S3_SECONDARY_ENDPOINT_URL/BUCKET_NAME/ACCESS_KEY_ID/SECRET_ACCESS_KEY`

#### Хранилище (`services/storage.py`)
- `setup_lifecycle_policy()` — вызывается при старте, ставит Lifecycle Rule с Expiration = 30 дней
  на префикс `musicbot/` (правило мержится с чужими правилами бакета)
- `upload_file()/download_file()/file_exists()/delete_file()` при сбоях связи/5xx
  автоматически переключаются Primary → Secondary; при недоступности обоих S3 —
  None/False + запись в лог (Graceful Fallback на локальный диск)

#### Загрузчик (`services/downloader.py`)
- `prune_cache()`: сначала безусловно удаляет mp3 старше `CACHE_MAX_AGE_DAYS` (по mtime),
  затем LRU-чистка по `CACHE_MAX_SIZE_BYTES`
- `DownloadResult.local_disposable`: после успешного push в S3 локальный mp3 удаляется
  сразу после отправки пользователю; если push не удался — файл остаётся локальным кэшем

### Этап 2 — PostgreSQL + Alembic
- `database/session.py`: для postgres(ql):// — asyncpg, `pool_pre_ping=True, pool_size=20, max_overflow=10`
- Alembic: `alembic.ini`, async `alembic/env.py` (URL из окружения/.env, без хардкода),
  начальная миграция `0001` (users, tracks, playlists, playlist_tracks, track_plays, favorites + индексы)
- `scripts/migrate_sqlite_to_postgres.py`: идемпотентный перенос (merge по первичному ключу)
  с коррекцией последовательностей `setval(COALESCE(MAX(id)+1))`

### Этап 3 — Redis FSM, network resilience, healthchecks
- `bot.py`:
  - `RetryOnNetworkError`: до 5 попыток с экспоненциальной задержкой (0.5с → 30с),
    покрывает обрывы/TLS/`TelegramNetworkError`/`aiohttp.ClientError`/OSError
  - Graceful shutdown по SIGINT/SIGTERM: `delete_webhook()` → остановка HTTP-сервера →
    закрытие Redis → `engine.dispose()` (каждый шаг независим от сбоев остальных)
  - `/healthz` доступен во всех режимах (webhook-only и polling без веб-плеера) —
    docker healthcheck всегда проходит
- `nginx`: таймауты `proxy_connect/send/read`, `proxy_http_version 1.1`, `proxy_next_upstream`,
  `proxy_buffering off` для стриминга веб-плеера; rate limit и X-Forwarded-For сохранены
- `nginx/Dockerfile`: установлен `curl` (healthcheck из docker-compose)

### Линтер
- `ruff.toml`: политика Graceful Degradation (BLE001 отключён, E722/S110 остаются)
- Исправлены 24 предсуществующие ошибки: TRY401, S110, SIM115/SIM117, C408, DTZ005,
  UP009/UP041, PLW1510; удалены устаревшие `noqa: BLE001`
- `ruff check .` — All checks passed

---

## [v1.1.0] - 2025-10-06

### Добавлено (Deployment)

#### Конфигурация
- Добавлены настройки webhook в `config.py`:
  - `WEBHOOK_ENABLED` - переключатель между webhook и polling режимами
  - `WEBHOOK_URL` - публичный HTTPS адрес бота
  - `WEBHOOK_PATH` - путь для webhook endpoint (default `/webhook`)
  - `WEBHOOK_SECRET` - секрет для валидации webhook (опционально)
- Добавлены настройки логирования:
  - `LOG_LEVEL` - уровень логирования (default `INFO`)
  - `LOG_FORMAT` - формат логов: `text` или `json` (default `text`)

#### Бот
- Добавлена поддержка webhook режима в `bot.py`:
  - Условная логика: webhook или polling в зависимости от `WEBHOOK_ENABLED`
  - Автоматическая установка webhook через `bot.set_webhook()`
  - Запуск aiohttp сервера для обработки webhook запросов
  - Корректный cleanup при остановке (удаление webhook)
  - Сохранение обратной совместимости с polling режимом
- Улучшено логирование:
  - Поддержка JSON формата (для Cloud Logging)
  - Настраиваемый уровень логирования через `LOG_LEVEL`
  - Fallback на text формат если `pythonjsonlogger` не установлен

#### Nginx
- Создана конфигурация nginx в `nginx/nginx.conf`:
  - Проксирование `/webhook` → musicbot контейнер
  - Проксирование `/app/*` → Web App
  - Проксирование `/api/*` → API endpoints
  - Проксирование `/healthz` → healthcheck
  - Rate limiting для защиты от DDoS
  - Заготовка для HTTPS (закомментирована)
- Создан `nginx/Dockerfile` на базе `nginx:alpine`

#### Docker
- Обновлён `Dockerfile`:
  - Добавлен `curl` для healthcheck
  - Оптимизированы слои (отдельный шаг для pip install)
  - Создание необходимых директорий (data, logs, downloads)
  - Добавлен healthcheck (curl to `/healthz`)
- Обновлён `docker-compose.yml`:
  - Добавлен сервис `nginx` с проксированием
  - Добавлены volumes для logs и downloads
  - Добавлена internal network для общения контейнеров
  - Добавлены healthcheck для обоих сервисов
  - Настроены restart policies

#### Файлы конфигурации
- Обновлён `.env.example`:
  - Добавлены webhook настройки с комментариями
  - Добавлены настройки логирования
  - Структурирован по секциям (Telegram, Spotify, Яндекс, БД, Кэш, Mini App, Webhook, Логирование)
- Создан `.dockerignore`:
  - Исключены `.env`, `.venv`, `__pycache__`
  - Исключены временные файлы и IDE конфиги
  - Исключены logs, data, downloads
  - Исключены документация и скрипты

#### Документация
- Создан `DEPLOY.md`:
  - Полная инструкция по деплою на Yandex Cloud
  - Требования к VM (2 vCPU, 2GB RAM, 15GB disk)
  - Установка Docker и Docker Compose
  - Получение SSL сертификатов (Lets Encrypt)
  - Настройка nginx для HTTPS
  - Управление контейнерами
  - Troubleshooting
  - Оптимизация для 2GB RAM
- Обновлён `DEPLOYMENT_TZ.md`:
  - Убраны выполненные задачи (webhook, nginx, docker)
  - Оставлены пост-деплой улучшения (S3, PostgreSQL, Redis)
  - Изменён фокус на масштабируемость после деплоя

### Изменено

#### Обратная совместимость
- Polling режим сохранён для локальной разработки
- Все новые настройки имеют значения по умолчанию
- Webhook включается только при `WEBHOOK_ENABLED=true`

### Технические детали

#### Зависимости
- Добавлен импорт `aiogram.webhook.aiohttp_server` в `bot.py`
- Добавлен `curl` в Dockerfile для healthcheck
- Опциональная зависимость `pythonjsonlogger` для JSON логов

#### Архитектура
- Контейнеры общаются через internal network (bridge)
- Nginx выступает как reverse proxy и HTTPS terminator
- Healthcheck endpoints доступны через nginx
- Логи пишутся в stdout (docker logs) и в файлы (rotate)

---

## [v1.0.0] - 2025-09-27

### Исходная версия
- Telegram бот для музыки с polling режимом
- Поиск через YT Music, SoundCloud, Spotify, Яндекс Музыка
- Inline режим
- Web App (Mini App)
- SQLite база данных
- Локальный кэш mp3 файлов
- Docker + docker-compose

---

## Данные для деплоя

### Обязательные:
1. **BOT_TOKEN** - токен от @BotFather
2. **SSH ключ** - для доступа к VM
3. **IP адрес VM** - публичный IP

### Опциональные:
1. **Домен** - для HTTPS и Web App
2. **SSL сертификат** - Lets Encrypt или собственный
3. **Spotify API ключи** - для полных плейлистов Spotify
4. **Яндекс Музыка токен** - для приватных плейлистов

### VM характеристики (уже создана):
- 2 vCPU
- 2 GB RAM
- 15 GB диск
- ОС: Ubuntu 22.04 LTS (рекомендуется)

---

## Следующие шаги (после деплоя)

Согласно `DEPLOYMENT_TZ.md`:

1. **S3 Integration** - кэш mp3 в Yandex Object Storage
2. **PostgreSQL подготовка** - адаптация кода для миграции
3. **Redis (опционально)** - для мультиинстансности

См. `DEPLOYMENT_TZ.md` для деталей.
