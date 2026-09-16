# 🎵 tg-music-bot

Telegram-бот для поиска, скачивания и прослушивания музыки. Полностью асинхронный, с веб-плеером (Mini App), фоновыми очередями, голосовым поиском, текстами песен, радио и автоматизацией 24/7.

> ⚠️ Дисклеймер: скачивание защищённой авторским правом музыки может нарушать ToS YouTube/Spotify и законы вашей страны. Бот предназначен для личного и образовательного использования. Авторы не несут ответственности за misuse.

---

## 📋 Возможности

### 🔍 Поиск и загрузка
- Текстовый поиск по названию/артисту
- Ссылки на YouTube / YouTube Music / SoundCloud / Spotify
- Источники: YT Music (основной), SoundCloud (fallback), yt-dlp (скачивание)
- Умный подбор лучшего результата через rapidfuzz (название + артист + длительность)
- Кэш file_id — повторные запросы не качают заново
- LRU-очистка кэша плеера (лимит 1.5 ГБ)

### ▶️ Воспроизведение
- Фоновая очередь треков: ⏭ далее / 🔀 shuffle / ⏹ стоп / ⏱ sleep-timer (+1/+3/+5)
- /now — что сейчас играет и позиция в очереди
- Избранное (❤️) и плейлисты (создание, добавление, удаление)
- Пагинация результатов поиска (по 8 на страницу)

### 🎤 Доп. фишки
- Inline-режим — @твойбот запрос в любом чате → трек прямо туда
- Голосовой поиск — отправьте voice с играющей песней, бот распознает (faster-whisper, опционально)
- 📝 Текст песни — через LRCLIB (без ключей)
- 📻 Радио — очередь похожей музыки (Deezer + YT fallback)
- ID3-теги + обложка 600×600 из iTunes Search API
- ⚙️ Настройки: качество 128/192/320 kbps, защита контента от пересылки
- 🏆 /top — глобальный и личный топ треков
- 🔥 Message effects в приватных чатах

### 📱 Mini App (веб-плеер)
- Кнопка меню в Telegram → открывает веб-плеер
- Вкладки «Избранное» / «Плейлисты» / поиск
- Стриминг mp3 с сервера, тема под Telegram
- /healthz — healthcheck для мониторинга

### 🛠 Автоматизация 24/7
- Вечный сторож start_bot.bat — авто-рестарт + уведомления о крашах
- Самоисцеляющийся туннель start_tunnel.ps1 — авто-обновление WEBAPP_URL
- Ночное обновление yt-dlp и др. (update_deps.bat в 04:00)
- Ежедневный бэкап БД (backup_db.ps1 в 04:30, хранит 14 дней)

---

## 🏗 Архитектура

tg-music-bot/
├── bot.py                  # Entrypoint: Bot+Dispatcher, loguru, graceful shutdown
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
│   │                       #   PlaylistTrack, TrackPlay, Settings
│   ├── repo.py             # async CRUD
│   └── session.py          # engine + SessionFactory + init_db()
│
├── services/
│   ├── searcher.py         # YT Music + SoundCloud + resolve_url (yt-dlp + spotipy)
│   ├── downloader.py       # yt-dlp + semaphore + LRU кэш
│   ├── sender.py           # send_track + FIRE_EFFECT
│   ├── player.py           # PlayerSession (очередь, shuffle, sleep-timer)
│   ├── matcher.py          # pick_best через rapidfuzz
│   ├── tagger.py           # ID3 + обложки из iTunes
│   ├── lyrics.py           # LRCLIB
│   └── radio.py            # Deezer
│
├── handlers/
│   ├── common.py           # /start, /help, deep-link
│   ├── search.py           # поиск + пагинация
│   ├── player.py           # /now, очередь, sleep-timer
│   ├── playlists.py        # /playlists, /new
│   ├── favorites.py        # /fav, ❤️
│   ├── inline.py           # inline-режим
│   ├── voice.py            # голосовой поиск
│   ├── track_tools.py      # 📝 тексты, 📻 радио
│   ├── settings.py         # /settings
│   └── stats.py            # /top
│
├── middlewares/
│   └── throttle.py         # anti-flood (5 событий / 4 сек)
│
├── webapp/
│   ├── app.py              # FastAPI: /app/, /stream/{id}, /api/tracks, /healthz
│   └── static/index.html   # веб-плеер
│
└── scripts/                # Windows-автопилот 24/7
    ├── start_bot.bat
    ├── kill_bot.ps1
    ├── update_deps.bat
    ├── backup_db.ps1
    └── start_tunnel.ps1

Async-стек: aiogram 3 · SQLAlchemy 2 async + aiosqlite · FastAPI · uvicorn · loguru

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
notepad .env   # заполнить BOT_TOKEN, ADMIN_TELEGRAM_ID

### Минимальный .env

BOT_TOKEN=123456:ABC-ваш-токен-от-BotFather
ADMIN_TELEGRAM_ID=0
API_ENABLED=true
API_HOST=0.0.0.0
API_PORT=8080
WEBAPP_URL=
DB_PATH=data/musicbot.db
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

## 🌐 Деплой 24/7

### Вариант А — Домашний ноут (Windows)

Бесплатно, но зависит от питания/интернета/туннеля. Полная инструкция — в scripts/README.md.

Кратко:
1. Настроить питание: крышка → «Действие не предпринимать», сон → «Никогда».
2. Создать 4 задачи в Планировщике (taskschd.msc):

| Задача | Триггер | Действие |
|---|---|---|
| MusicBot | При запуске системы | scripts\start_bot.bat |
| MusicTunnel | При запуске системы | powershell -File scripts\start_tunnel.ps1 |
| MusicUpdate | Ежедневно 04:00 | scripts\update_deps.bat |
| MusicBackup | Ежедневно 04:30 | powershell -File scripts\backup_db.ps1 |

3. В .env указать ADMIN_TELEGRAM_ID (узнать у @userinfobot) — будете получать уведомления о рестартах/крашах.

### Вариант Б — VPS (Docker)

Стабильно, ~400₽/мес, не зависит от домашних условий.

git clone https://github.com/HOGAky/tg-music-bot.git
cd tg-music-bot
cp .env.example .env  # заполнить
docker compose up -d --build

Caddy автоматически выпустит Let's Encrypt-сертификат. В Caddyfile поменяйте домен на свой.

---

## ⚙️ Переменные окружения

| Переменная | Тип | По умолчанию | Описание |
|---|---|---|---|
| BOT_TOKEN | str | — | Токен от @BotFather (обязательно) |
| ADMIN_TELEGRAM_ID | int | 0 | Ваш Telegram ID для уведомлений |
| API_ENABLED | bool | true | Запускать FastAPI Mini App |
| API_HOST | str | 0.0.0.0 | Хост веб-плеера |
| API_PORT | int | 8080 | Порт веб-плеера |
| WEBAPP_URL | str | "" | Публичный HTTPS-адрес плеера (<host>/app/) |
| DB_PATH | str | data/musicbot.db | Путь к SQLite |
| CACHE_DIR | str | data/cache | Кэш mp3 |
| MAX_CONCURRENT_DOWNLOADS | int | 2 | Лимит параллельных yt-dlp |

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
- Settings — per-user настройки (quality, protect_content)

---

## 🩺 Известные ограничения

- SQLite single-instance — потолок параллелизма; для масштаба нужен PostgreSQL.
- yt-dlp ломается раз в 2 недели — ночные обновления (update_deps.bat) mitigating, но не гарантируют.
- Webapp без авторизации — любой с URL туннеля может пользоваться плеером. TODO: валидация Telegram initData HMAC.
- faster-whisper "tiny" плохо распознаёт напевание — лучше для записанного аудио. Альтернатива: AcoustID/Chromaprint (Shazam-mode).
- Inline-кэш показывает только треки, которые кто-то уже слушал через бота — ограничение Telegram (бот не отдаёт файл, которого у него нет).

---

## 🗺 Roadmap

- [ ] Авторизация веб-плеера через Telegram initData (HMAC)
- [ ] Shazam-режим (AcoustID + chromaprint)
- [ ] Telegram Stars premium-тир (приоритетная очередь, 320 по умолчанию)
- [ ] Совместные плейлисты (deep-link инвайты)
- [ ] Redis для FSM + кэш поиска (мультиинстанс)
- [ ] PostgreSQL + Alembic миграции
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
- Deezer API — радио
- iTunes Search API — обложки
- Cloudflare / localhost.run — туннели
- faster-whisper — распознавание голоса
- loguru — логирование


ВСЕ ВКЛЮЧАЯ ЭТОТ README СОЗДАНО С ПОМОЩЬЮ z.ai, в образовательных целях.
