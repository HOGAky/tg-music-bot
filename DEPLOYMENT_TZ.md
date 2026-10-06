# Техническое задание: Пост-деплой улучшения tg-music-bot

## 📋 Обзор

**Цель:** После успешного деплоя на Yandex Compute Cloud, улучшить архитектуру бота для лучшей масштабируемости и надёжности.

**Текущее состояние (после деплоя):**
- ✅ Бот работает в webhook режиме
- ✅ Nginx настроен для HTTPS termination
- ✅ Docker-compose настроен с healthcheck
- ✅ Логирование настроено (text/json формат)
- ✅ Инструкция по деплою (DEPLOY.md) создана

**Целевое состояние:**
- Кэш mp3 в Yandex Object Storage (S3-совместимый)
- Подготовка к миграции на PostgreSQL
- Мультиинстансность через Redis (опционально)

---

## 🎯 Критерии успеха

1. ✅ MP3 файлы кэшируются в Yandex Object Storage
2. ✅ Fallback на локальное хранилище при недоступности S3
3. ✅ Код поддерживает PostgreSQL (без фактической миграции)
4. ✅ Конфигурация через .env без hardcoded значений

---

## 📝 Требования к изменениям

### 1. Интеграция с Yandex Object Storage (S3)

**Описание:** Перенести локальный кэш mp3 файлов в Yandex Object Storage для персистентности и масштабируемости.

**Задачи:**

#### 1.1. Добавить S3 storage service
- [ ] Создать файл `services/storage.py`
- [ ] Реализовать класс `S3Storage` с методами:
  - `upload_file(file_path: str, key: str) -> str` - загрузка файла
  - `download_file(key: str, local_path: str) -> str` - скачивание файла
  - `file_exists(key: str) -> bool` - проверка существования
  - `delete_file(key: str) -> None` - удаление файла
  - `get_public_url(key: str, ttl: int = 3600) -> str` - получение публичной ссылки
- [ ] Использовать библиотеку `boto3` для работы с S3
- [ ] Поддерживать fallback на локальное хранилище (если S3 недоступен)

**Технические детали:**
- Использовать boto3 с S3-совместимым endpoint
- Обрабатывать ошибки подключения к S3
- Кэшировать S3 key в локальном файле (для быстрого доступа)
- Использовать префикс `musicbot/` для изоляции файлов

#### 1.2. Обновить `services/downloader.py`
- [ ] Добавить зависимость от `S3Storage`
- [ ] После скачивания файла → загружать в S3
- [ ] При повторном запросе → проверять S3 кэш перед скачиванием
- [ ] Очистка локального кэша после успешной загрузки в S3
- [ ] Сохранять S3 key в Track модели вместо локального пути

#### 1.3. Обновить `config.py`
- [ ] Добавить поля:
  ```python
  USE_S3_STORAGE: bool = False
  S3_ENDPOINT_URL: str = ""  # e.g., https://storage.yandexcloud.net
  S3_BUCKET_NAME: str = ""
  S3_ACCESS_KEY_ID: str = ""
  S3_SECRET_ACCESS_KEY: str = ""
  S3_REGION: str = "ru-central1"
  S3_CACHE_PREFIX: str = "musicbot/"
  ```

#### 1.4. Обновить `requirements.txt`
- [ ] Добавить `boto3>=1.34.0`

#### 1.5. Обновить `.env.example`
- [ ] Добавить S3 настройки с комментариями

#### 1.6. Обновить `database/models.py`
- [ ] В модели `Track` добавить поле `s3_key: str | None = None`
- [ ] Миграция: существующие записи с локальными путями (опционально)

---

### 2. Подготовка к PostgreSQL

**Описание:** Подготовить код к миграции с SQLite на PostgreSQL без фактической миграции.

**Задачи:**

#### 2.1. Обновить `database/session.py`
- [ ] Добавить поддержку PostgreSQL через `asyncpg`
- [ ] Автоматический выбор драйвера на основе DB_URL
- [ ] SQLite → aiosqlite, PostgreSQL → asyncpg

**Технические детали:**
- Проверять DB_URL на наличие `postgresql://` или `postgres://`
- Если PostgreSQL → использовать asyncpg
- Если SQLite → использовать aiosqlite
- Сохранить обратную совместимость

#### 2.2. Обновить `requirements.txt`
- [ ] Добавить `asyncpg>=0.29.0` (conditional import в коде)

#### 2.3. Обновить `.env.example`
- [ ] Добавить пример PostgreSQL connection string с комментарием

#### 2.4. Создать скрипт миграции (опционально)
- [ ] Создать `scripts/migrate_to_postgres.sh`
- [ ] Скрипт для экспорта SQLite и импорта в PostgreSQL
- [ ] Инструкция по созданию PostgreSQL инстанса в Yandex Cloud

---

### 3. Redis для FSM и кэша (опционально)

**Описание:** Добавить Redis для поддержки мультиинстансности и кэширования.

**Задачи:**

#### 3.1. Обновить `config.py`
- [ ] Добавить поля:
  ```python
  REDIS_URL: str = ""  # e.g., redis://localhost:6379/0
  USE_REDIS: bool = False
  ```

#### 3.2. Обновить `requirements.txt`
- [ ] Добавить `redis>=5.0.0`

#### 3.3. Интегрировать Redis в aiogram FSM
- [ ] Использовать RedisStorage для aiogram FSM
- [ ] Fallback на MemoryStorage если Redis недоступен

#### 3.4. Обновить `docker-compose.yml`
- [ ] Добавить Redis сервис:
  ```yaml
  redis:
    image: redis:alpine
    restart: always
    volumes:
      - redis_data:/data
  ```

---

## 🚀 Порядок реализации

Рекомендуемый порядок (по приоритету):

1. **Этап 1: S3 Integration** (самое важное для масштабируемости)
   - Создать storage.py
   - Обновить downloader.py
   - Обновить модели БД
   - Тестировать с MinIO локально или тестовым бакетом

2. **Этап 2: PostgreSQL подготовка**
   - Добавить поддержку asyncpg
   - Без фактической миграции
   - Тестировать подключение к PostgreSQL

3. **Этап 3: Redis (опционально)**
   - Только если нужна мультиинстансность
   - Добавить RedisStorage для FSM

---

## ✅ Приёмка

Для каждого этапа:

1. Код проходит `ruff check`
2. Бот запускается без ошибок
3. Новая функциональность работает корректно
4. Fallback режимы работают (если S3 недоступен → локальный кэш)
5. Обратная совместимость сохранена

---

## 📌 Примечания

- Сохранять обратную совместимость с локальным хранилищем
- Все секреты через .env, ни одного hardcoded значения
- Fallback для облачных сервисов (если S3 недоступен → локальный кэш)
- PostgreSQL миграция → отдельная задача с резервным копированием

---

## 🔗 Полезные ссылки

- [Yandex Object Storage документация](https://cloud.yandex.ru/docs/storage/)
- [Yandex Managed Service for PostgreSQL](https://cloud.yandex.ru/docs/managed-postgresql/)
- [Yandex Managed Service for Redis](https://cloud.yandex.ru/docs/managed-redis/)
- [boto3 S3 документация](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)
- [aiogram RedisStorage](https://docs.aiogram.dev/en/dev/dispatcher/fsm.html#redis-storage)
