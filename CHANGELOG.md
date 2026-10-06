# Changelog

Все изменения проекта для деплоя на Yandex Cloud.

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
