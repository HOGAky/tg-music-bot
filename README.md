# 🎵 tg-music-bot

Telegram-бот для поиска, скачивания и прослушивания музыки. Полностью асинхронный, с веб-плеером (Mini App), текстами песен, радио и inline-режимом.

> ⚠️ Дисклеймер: скачивание защищённой авторским правом музыки может нарушать ToS YouTube/Spotify и законы вашей страны. Бот предназначен для личного и образовательного использования. Авторы не несут ответственности за misuse.

---

## 📋 Возможности

### 🔍 Поиск и загрузка
- Текстовый поиск по названию/артисту
- Ссылки на YouTube / YouTube Music / SoundCloud / Spotify / Яндекс Музыку
- Плейлисты и альбомы целиком: Spotify (нужны API-ключи, без них ~50 треков
  через embed) и Яндекс Музыка (оба формата ссылок: старые
  `users/<login>/playlists/<id>` и новые «поделиться» `/playlists/<uuid>`) —
  кнопка «⬇️ Скачать все» с прогрессом и остановкой, до 5000 треков;
  плейлисты больше 50 треков приходят zip-архивами по ~40 МБ
  (лимит Telegram на файл для ботов — 50 МБ)
- Источники: YT Music (основной), SoundCloud (fallback), yt-dlp (скачивание);
  треки Spotify/Яндекса качаются через поиск на YT Music
- Кэш file_id — повторные запросы не качают заново
- LRU-очистка локального кэша mp3 (лимит 1.5 ГБ)

### ▶️ Воспроизведение
- /now — что сейчас играет и позиция в очереди плейлиста
- Избранное (❤️) и плейлисты (создание, добавление, удаление)
- Пагинация результатов поиска

### 🎤 Доп. фишки
- Inline-режим — @твойбот запрос в любом чате (только уже закэшированные треки)
- 📝 Текст песни — через LRCLIB (без ключей)
- 📻 Радио — похожие треки из YT Music (watch playlist + поиск как fallback)
- ⚙️ /settings — качество 128/192/320 kbps, защита контента от пересылки
- 🏆 /top — глобальный и личный топ треков

### 🛡️ Продакшен и эксплуатация
- Docker Compose с PostgreSQL и Redis для состояния FSM
- Webhook-обработка с ограничением параллельных обновлений и healthcheck зависимостей
- Отдельный Telegram-бот получает WARNING/ERROR логи и уведомления о сбоях/восстановлении
- Мониторинг запускается параллельно основному боту из `bot.py`
- systemd для запуска при перезагрузке, проверок обновлений и ежедневных резервных копий

### 📱 Mini App (веб-плеер)
- Включается через `API_ENABLED=true`; при заданном `WEBAPP_URL` бот ставит кнопку меню
- Избранное и плейлисты, стриминг mp3, тема под Telegram
- Авторизация по подписи Telegram WebApp initData
- В production Docker Compose Mini App выключена; `/healthz` работает отдельно

Пока не реализовано: голосовой поиск, ID3-теги/обложки, sleep-timer, поиск внутри Mini App.

---

## 🏗 Архитектура

tg-music-bot/
├── bot.py                  # Entrypoint: Bot+Dispatcher, запуск Mini App
├── config.py               # pydantic-settings Config
├── texts.py                # RU-шаблоны сообщений
├── utils.py                # helpers: short(), pack_query, msg_of, safe_edit
├── callbacks.py            # aiogram CallbackData classes
├── keyboards.py            # inline-клавиатуры
├── requirements.txt
├── .env.example
│
├── database/
│   ├── models.py           # SQLAlchemy 2 Mapped: User, Track, Favorite, Playlist,
│   │                       #   PlaylistTrack, TrackPlay
│   ├── repo.py             # async CRUD
│   └── session.py          # engine + SessionFactory + init_db()
│
├── services/
│   ├── searcher.py         # YT Music + SoundCloud + resolve_url (yt-dlp + spotipy + yandex-music)
│   ├── downloader.py       # yt-dlp + semaphore + LRU кэш
│   ├── sender.py           # send_track (качество, protect_content, статистика)
│   ├── player.py           # now playing по пользователям
│   ├── lyrics.py           # LRCLIB
│   └── radio.py            # похожие треки из YT Music
│
├── handlers/
│   ├── common.py           # /start, /help
│   ├── search.py           # поиск + пагинация
│   ├── player.py           # /now, /top, /settings
│   ├── playlists.py        # /playlists, /new
│   ├── favorites.py        # /fav, ❤️
│   ├── inline.py           # inline-режим
│   └── track_tools.py      # 📝 тексты, 📻 радио
│
└── webapp/
    ├── app.py              # aiohttp: /app, /api/library, /api/stream/{id}, /healthz
    └── static/index.html   # веб-плеер

Async-стек: aiogram 3 · SQLAlchemy 2 async + aiosqlite · aiohttp · yt-dlp

---

## 🚀 Быстрый старт

### Требования
- Python 3.12+
- ffmpeg (для конвертации в mp3)
- Telegram-бот через @BotFather → /newbot

### Установка (Windows, PowerShell)

git clone https://github.com/HOGAky/tg-music-bot.git
cd tg-music-bot

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

copy .env.example .env
notepad .env   # заполнить BOT_TOKEN

### Минимальный .env

BOT_TOKEN=123456:ABC-ваш-токен-от-BotFather
DB_URL=sqlite+aiosqlite:///data/musicbot.db
CACHE_DIR=data/cache
MAX_CONCURRENT_DOWNLOADS=2

### Включить inline-режим (один раз)
1. @BotFather → /setinline → выбрать бота
2. Placeholder: название трека...

### Запуск

.\.venv\Scripts\python.exe bot.py

В Telegram → чат с ботом → кнопка меню слева → 🎵 Плеер → откроется веб-плеер.

---

## 📋 Команды

| Команда | Действие |
|---|---|
| /start | Приветствие + подсказки |
| /help | То же, что /start |
| /fav | Избранное (с кнопкой 🔀 перемешать) |
| /playlists | Ваши плейлисты |
| /new | Создать плейлист |
| /now | Что сейчас играет + позиция |
| /top | Топ-10 треков бота + ваш личный топ-5 |
| /settings | Качество 128/192/320, защита контента |

Под треком: ❤️ избранное · 📝 текст · 📻 радио · 📁 в плейлист · ↗️ поделиться

В любом чате: @юзернейм_бота blinding lights → трек прямо в чат

---

## 🌐 Mini App

1. `API_ENABLED=true` в .env — бот поднимет aiohttp на `API_HOST:API_PORT`.
2. Прокинуть публичный HTTPS (туннель или reverse proxy) и указать `WEBAPP_URL=https://.../app/`.
3. При старте бот сам ставит кнопку меню «🎵 Плеер».

API принимает только запросы с корректной подписью initData, иначе 401.

---

## ⚙️ Переменные окружения

| Переменная | Тип | По умолчанию | Описание |
|---|---|---|---|
| BOT_TOKEN | str | — | Токен от @BotFather (обязательно) |
| SPOTIFY_CLIENT_ID | str | "" | Spotify API (иначе fallback через oembed) |
| SPOTIFY_CLIENT_SECRET | str | "" | Spotify API |
| YANDEX_TOKEN | str | "" | Токен Яндекс Музыки: приватные плейлисты («Мне нравится» и т.п.); публичные работают без него |
| API_ENABLED | bool | false | Запускать Mini App |
| API_HOST | str | 0.0.0.0 | Хост веб-плеера |
| API_PORT | int | 8080 | Порт веб-плеера |
| WEBAPP_URL | str | "" | Публичный HTTPS-адрес плеера (<host>/app/) |
| DB_URL | str | sqlite+aiosqlite:///data/musicbot.db | SQLAlchemy-строка подключения (PostgreSQL: `postgresql+asyncpg://…`) |
| POSTGRES_PASSWORD | str | — | Пароль PostgreSQL в Docker Compose; сгенерируйте случайную hex-строку |
| DB_POOL_SIZE / DB_MAX_OVERFLOW / DB_POOL_TIMEOUT | int | 20 / 10 / 30 | Параметры пула PostgreSQL |
| CACHE_DIR | str | downloads | Кэш mp3 |
| MAX_CONCURRENT_DOWNLOADS | int | 4 | Лимит параллельных yt-dlp |
| MAX_CONCURRENT_UPDATES | int | 64 | Максимум одновременно обрабатываемых Telegram-обновлений |
| WEBHOOK_ENABLED | bool | false | Webhook вместо polling (продакшен за nginx) |
| WEBHOOK_URL / WEBHOOK_PATH / WEBHOOK_SECRET | str | "" | Адрес, путь и секрет webhook |
| MONITOR_BOT_TOKEN / MONITOR_CHAT_ID | str | "" | Токен отдельного бота и (необязательно) ID разрешённого чата для логов и сообщений о сбоях |
| MONITOR_INTERVAL_SECONDS | int | 30 | Частота проверки health endpoint монитором в `bot.py` |
| LOG_LEVEL / LOG_FORMAT | str | INFO / text | Уровень и формат логов (json для облака) |
| USE_S3_STORAGE | bool | false | Кэш mp3 в Yandex Object Storage; при недоступности S3 — локальный кэш |
| S3_ENDPOINT_URL / S3_BUCKET_NAME / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY / S3_REGION / S3_CACHE_PREFIX | str | — | Настройки S3 (см. `.env.example`) |
| USE_REDIS / REDIS_URL | bool/str | false / "" | Redis для FSM; в Docker Compose включён и обязателен |

Если задан `MONITOR_BOT_TOKEN`, `python bot.py` запускает обоих ботов: основной
работает как прежде, а отдельный бот уведомлений параллельно запускает polling и
пересылку логов. Отправьте ему `/start`; команда `/status` проверяет состояние
основного бота. Для ограничения доступа заранее укажите свой ID в
`MONITOR_CHAT_ID`. Если ID не задан, монитор-бот подключит чат, который первым
отправит ему `/start`; подключённый чат хранится только до перезапуска процесса.
Пересылаются `WARNING` и `ERROR`, а `INFO`/`DEBUG` остаются в локальных логах.

Если основной источник не отдаёт аудио, бот пробует SoundCloud только для
треков, у которых автор явно разрешил скачивание. Результаты YouTube Music
остаются основными для поиска; если загрузка не проходит, SoundCloud ищет
доступную версию по названию и исполнителю. Доступность конкретной песни зависит
от каталога SoundCloud.

Инструкции для Linux-хоста, systemd, автоматических обновлений и резервных копий —
в [DEPLOY.md](./DEPLOY.md). Один хост остаётся единой точкой отказа; указанная там
ресурсная конфигурация является стартовой и не заменяет нагрузочное тестирование.

---

## 🛠 Разработка

# Установить dev-зависимости
.\.venv\Scripts\python.exe -m pip install ruff pytest pytest-asyncio

# Линт
ruff check .

# Тесты
pytest

### Структура БД (SQLAlchemy 2 Mapped)
- User — пользователи
- Track — кэш треков (file_id, download_url)
- Favorite — избранное (unique user+track)
- Playlist / PlaylistTrack — плейлисты
- TrackPlay — лог прослушиваний (для /top)

Настройки (quality, protect_content) хранятся полями User.

---

## 🩺 Известные ограничения

- Spotify-плейлисты/альбомы без API-ключей работают через публичную embed-страницу — только первые ~50 треков; с ключами (`SPOTIFY_CLIENT_ID/SECRET`) — список целиком.
- Треки Яндекс Музыки качаются не напрямую, а через повторный поиск на YT Music — редкие треки могут не найтись.
- Новые ссылки Яндекса `/playlists/<uuid>` резолвятся через HTML страницы (API по uuid плейлист не отдаёт) — может сломаться при изменениях на стороне Яндекса.
- Пакетное скачивание живёт в памяти процесса: после рестарта бота кнопка «⬇️ Скачать все» на старых сообщениях перестаёт работать.
- SQLite single-instance — потолок параллелизма; для масштаба нужен PostgreSQL.
- yt-dlp ломается раз в 2 недели — нужны регулярные обновления зависимостей.
- Смена качества в /settings влияет только на новые загрузки: уже закэшированный file_id отдаётся как есть.
- /now хранится в памяти процесса и сбрасывается при рестарте бота.
- Inline-кэш показывает только треки, которые кто-то уже слушал через бота — ограничение Telegram (бот не отдаёт файл, которого у него нет).

---

## 🗺 Roadmap

- [x] Авторизация веб-плеера через Telegram initData (HMAC)
- [ ] Голосовой поиск и Shazam-режим (AcoustID + chromaprint)
- [ ] Telegram Stars premium-тир (приоритетная очередь, 320 по умолчанию)
- [ ] Совместные плейлисты (deep-link инвайты)
- [x] Redis для FSM в production Docker Compose
- [x] PostgreSQL для production Docker Compose
- [ ] Apple Music-ссылки (iTunes Lookup API)
- [ ] Автопостинг «трек дня» в канал
- [ ] Web Player 2.0: поиск в Mini App, редактирование плейлистов
- [ ] /stats_admin дашборд (DAU, топ запросов, ошибки)
- [ ] Тесты + GitHub Actions CI

---

## 📄 Лицензия

MIT — см. LICENSE.

Используя бота, вы соглашаетесь, что несёте полную ответственность за соблюдение авторских прав и ToS используемых сервисов (YouTube, Spotify, SoundCloud) в вашей юрисдикции.

---

## 🙏 Благодарности

- aiogram — async Telegram Bot API framework
- yt-dlp — загрузка медиа
- LRCLIB — тексты песен
- ytmusicapi — поиск и радио
- Cloudflare / localhost.run — туннели


ВСЕ ВКЛЮЧАЯ ЭТОТ README СОЗДАНО С ПОМОЩЬЮ z.ai, в образовательных целях.
