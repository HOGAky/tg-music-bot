# Деплой tg-music-bot на Yandex Cloud

## 📋 Требования

### Необходимые данные для деплоя:

1. **Telegram Bot Token** - от @BotFather
2. **SSH доступ к VM** - публичный IP адрес и SSH ключ
3. **Домен** с DNS-записью на VM
4. **SSL сертификат** (например, Let's Encrypt) для Telegram webhook

### Характеристики VM:
- от 4 vCPU
- от 8 GB RAM
- от 30 GB диск (больше, если хранить локальный кэш и бэкапы)
- Ubuntu 22.04 LTS (рекомендуется)

Это стартовая конфигурация для заявленного ориентира около 1000 пользователей,
но не гарантия для 1000 одновременных запросов или загрузок. Без уточнённого
пикового числа одновременных пользователей пропускную способность нужно подтвердить
нагрузочным тестом с учётом лимитов источников музыки. Одна VM остаётся единой
точкой отказа.

---

## 🚀 Пошаговая инструкция

### Шаг 1: Подготовка локального проекта

#### 1.1. Скопировать `.env.example` в `.env`

```bash
cp .env.example .env
```

#### 1.2. Заполнить `.env`

Обязательные переменные:
```bash
BOT_TOKEN=123456:ABC-ваш-токен-от-BotFather
POSTGRES_PASSWORD=<случайная hex-строка из openssl rand -hex 32>
WEBHOOK_ENABLED=true
WEBHOOK_URL=https://ваш-домен
WEBHOOK_PATH=/webhook
WEBHOOK_SECRET=<случайная hex-строка из openssl rand -hex 32>
MONITOR_BOT_TOKEN=<токен отдельного бота уведомлений>
MONITOR_CHAT_ID=<id чата, куда отправлять уведомления>
```

Создайте отдельного бота уведомлений через BotFather и сначала отправьте ему
`/start`; укажите ID личного или группового чата в `MONITOR_CHAT_ID`, чтобы
ограничить доступ этим чатом. Если ID не указан, бот запустится, а чат для
уведомлений подключится по первому `/start`; после каждого перезапуска нужно
снова отправить `/start`.
Храните `.env` только на VM с правами `chmod 600 .env`; не добавляйте его в Git.
Опциональные переменные:
```bash
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
YANDEX_TOKEN=
MAX_CONCURRENT_UPDATES=64
LOG_LEVEL=INFO
LOG_FORMAT=text
```

Mini App намеренно выключена в Docker Compose. Healthcheck доступен отдельно
по `/healthz` и не показывает кнопку Mini App в Telegram.

#### 1.3. Тестовая сборка локально (опционально)

```bash
docker compose up -d --build
```

---

### Шаг 2: Подключение к VM

```bash
ssh -i ~/.ssh/your-key.pem username@ваш-ip-vm
```

Замените:
- `~/.ssh/your-key.pem` - путь к вашему SSH ключу
- `username` - имя пользователя (обычно `ubuntu` или `yc-user`)
- `ваш-ip-vm` - публичный IP адрес вашей VM

---

### Шаг 3: Установка Docker на VM

#### 3.1. Обновить систему

```bash
sudo apt update && sudo apt upgrade -y
```

#### 3.2. Установить Docker

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

#### 3.3. Установить Docker Compose

```bash
sudo apt install docker-compose-plugin -y
```

#### 3.4. Добавить пользователя в docker группу

```bash
sudo usermod -aG docker $USER
```

Выйти и зайти снова для применения изменений.

#### 3.5. Проверить установку

```bash
docker --version
docker compose version
```

---

### Шаг 4: Подготовка проекта на VM

#### 4.1. Создать директорию для проекта

```bash
mkdir -p ~/musicbot
cd ~/musicbot
```

#### 4.2. Загрузить файлы на VM

**Вариант А: Через git (если проект в публичном репозитории)**

```bash
git clone https://github.com/HOGAky/tg-music-bot.git .
```

**Вариант Б: Через scp (с локальной машины)**

```bash
scp -i ~/.ssh/your-key.pem -r . username@ваш-ip-vm:~/musicbot/
```

**Вариант В: Через rsync (рекомендуется)**

```bash
rsync -avz -e "ssh -i ~/.ssh/your-key.pem" \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.git' \
  --exclude 'data' \
  --exclude 'logs' \
  --exclude 'downloads' \
  . username@ваш-ip-vm:~/musicbot/
```

#### 4.3. Создать `.env` на VM

```bash
nano .env
```

Вставьте содержимое вашего `.env` файла.

#### 4.4. Создать необходимые директории

```bash
mkdir -p data logs downloads nginx/ssl
```

---

### Шаг 5: Получение SSL сертификата

Для production webhook нужен домен с HTTPS. Убедитесь, что DNS указывает на VM и
порт 80 доступен для первичного ACME challenge.

#### 5.1. Установить certbot

```bash
sudo apt install certbot -y
```

#### 5.2. Получить сертификат

```bash
sudo certbot certonly --standalone -d ваш-домен.com
```

#### 5.3. Скопировать сертификаты в nginx директорию

```bash
sudo install -d -m 0755 /opt/tg-music-bot/nginx/ssl/live/ваш-домен.com
sudo install -m 0644 /etc/letsencrypt/live/ваш-домен.com/fullchain.pem \
  /opt/tg-music-bot/nginx/ssl/live/ваш-домен.com/fullchain.pem
sudo install -m 0600 /etc/letsencrypt/live/ваш-домен.com/privkey.pem \
  /opt/tg-music-bot/nginx/ssl/live/ваш-домен.com/privkey.pem
```

#### 5.4. Настроить автообновление

Настройте deploy-hook для `certbot renew`, который копирует обновлённые сертификаты
в `/opt/tg-music-bot/nginx/ssl/live/ваш-домен.com/` и выполняет
`docker compose -f /opt/tg-music-bot/docker-compose.yml restart nginx`.


### Шаг 6: Настройка nginx для HTTPS

HTTPS-конфигурация уже включена в `nginx/nginx.conf`. Укажите домен и пути к
скопированным сертификату и приватному ключу:

```bash
nano nginx/nginx.conf
```

Замените `84.201.167.96.sslip.io` в путях `ssl_certificate` и
`ssl_certificate_key` на ваш домен. HTTP уже перенаправляется на HTTPS.

---

### Шаг 7: Запуск контейнеров

#### 7.1. Собрать образы

```bash
docker compose build
```

#### 7.2. Запустить в фоне

```bash
docker compose up -d
```

#### 7.3. Проверить статус

```bash
docker compose ps
```

#### 7.4. Посмотреть логи

```bash
# Все логи
docker compose logs -f

# Логи только бота
docker compose logs -f musicbot

# Логи nginx
docker compose logs -f nginx
```

---

### Шаг 8: Проверка работы

#### 8.1. Проверить healthcheck

```bash
curl http://localhost/healthz
```

#### 8.2. Проверить webhook

```bash
curl http://localhost/webhook
```

Должен вернуть 405 Method Not Allowed (нормально для GET запроса).

#### 8.3. Проверить бота в Telegram

- Напишите `/start` боту
- Проверьте, что бот отвечает

#### 8.4. Проверить уведомителя

После первой успешной проверки монитор отправит сообщение о состоянии сервиса
настроенному чату. При сбое `/healthz` монитор отправит уведомление, а после
восстановления — отдельное сообщение.

---

### Шаг 9: Автозапуск, обновления и резервные копии

Шаблоны systemd рассчитаны на каталог `/opt/tg-music-bot`. Убедитесь, что
репозиторий, `.env` и SSL-сертификаты находятся там; `.env` должен содержать
значения `MONITOR_BOT_TOKEN` и `MONITOR_CHAT_ID`. Мониторинг и пересылка
WARNING/ERROR логов запускаются внутри `bot.py`, вместе с основным ботом.

```bash
cd /opt/tg-music-bot
sudo install -m 0644 deploy/systemd/tg-music-bot.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/musicbot-update.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/musicbot-update.timer /etc/systemd/system/
sudo install -m 0644 deploy/systemd/musicbot-backup.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/musicbot-backup.timer /etc/systemd/system/
sudo install -m 0644 deploy/systemd/musicbot-watchdog.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/musicbot-watchdog.timer /etc/systemd/system/
sudo install -m 0644 deploy/systemd/certbot-renew.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/certbot-renew.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tg-music-bot.service
sudo systemctl enable --now musicbot-update.timer musicbot-backup.timer musicbot-watchdog.timer certbot-renew.timer
```

Ротация логов Docker: скопируйте `deploy/daemon.json` в `/etc/docker/daemon.json`
и перезапустите Docker — иначе json-file логи контейнеров растут без ограничений.

```bash
sudo install -m 0644 deploy/daemon.json /etc/docker/daemon.json
sudo systemctl restart docker
```

- Docker перезапускает контейнеры после аварийного завершения и старта Docker после перезагрузки хоста.
- Watchdog проверяет Docker healthcheck раз в минуту и перезапускает контейнер бота,
  если он признан нездоровым.
- `certbot-renew.timer` раз в сутки запускает `certbot renew --webroot`; при успешном
  продлении deploy-hook перезагружает nginx, простой не требуется.
- Сервис обновлений проверяет `origin/main` каждые 5 минут, собирает новую версию и
  проверяет `/healthz`. Если новая версия не проходит проверку, предыдущий образ
  восстанавливается; проблемный commit автоматически повторно не разворачивается.
- Обновление выполняется на самом хосте: GitHub Actions и SSH-ключи в GitHub не нужны.
  Для приватного репозитория настройте на VM read-only deploy key.
- PostgreSQL сохраняется в Docker volume. Ежедневный закрытый локальный дамп хранится
  в `backups/`, старше 14 дней автоматически удаляется. Резервная копия на том же
  диске не защищает от потери VM — регулярно копируйте дампы в отдельное хранилище
  и проверяйте восстановление.

Проверка дампов и восстановление на отдельной VM:

```bash
ls -lh /opt/tg-music-bot/backups/
docker compose stop musicbot
docker compose exec -T postgres dropdb -U musicbot --if-exists musicbot
docker compose exec -T postgres createdb -U musicbot musicbot
docker compose exec -T postgres pg_restore -U musicbot -d musicbot --no-owner \
  < /opt/tg-music-bot/backups/musicbot-YYYYMMDDTHHMMSSZ.dump
docker compose up -d musicbot
```

Восстановление заменяет текущую базу данных; проверяйте его на тестовой VM до
использования в production.

Полезные команды:

```bash
sudo systemctl status tg-music-bot.service
sudo systemctl list-timers musicbot-update.timer musicbot-backup.timer
sudo systemctl list-timers musicbot-watchdog.timer
sudo journalctl -u musicbot-update.service -n 100 --no-pager
sudo journalctl -u musicbot-backup.service -n 100 --no-pager
docker compose ps
curl -fsS https://ваш-домен/healthz
```

Ответ `/healthz` показывает состояния бота, PostgreSQL и Redis; HTTP 503 означает,
что хотя бы одна проверка не пройдена. Healthcheck сам не заменяет внешний
мониторинг и резервное копирование.

---

## 🔧 Управление

### Просмотр логов

```bash
docker compose logs -f musicbot
```

### Перезапуск бота

```bash
docker compose restart musicbot
```

### Перезапуск nginx

```bash
docker compose restart nginx
```

### Остановка всех контейнеров

```bash
docker compose down
```

### Остановка с удалением volumes

```bash
docker compose down -v
```

`down -v` удаляет volume PostgreSQL и Redis вместе с данными; не используйте
эту команду для обычной остановки или обновления.

### Обновление проекта

```bash
# При включённом musicbot-update.timer обновление выполняется автоматически.
# Для ручного обновления:
sudo systemctl start musicbot-update.service
```

---

## 📊 Мониторинг

### Проверить использование ресурсов

```bash
docker stats
```

### Проверить диск

```bash
df -h
```

### Проверить использование директорий

```bash
du -sh ~/musicbot/*
```

---

## 🔒 Безопасность

### Настроить firewall (UFW)

```bash
# Разрешить SSH
sudo ufw allow 22/tcp

# Разрешить HTTP
sudo ufw allow 80/tcp

# Разрешить HTTPS
sudo ufw allow 443/tcp

# Включить firewall
sudo ufw enable
```

### Ограничить доступ по SSH

В `/etc/ssh/sshd_config`:
```
PermitRootLogin no
PasswordAuthentication no
```

Перезапустить SSH:
```bash
sudo systemctl restart sshd
```

---

## 🐛 Troubleshooting

### Бот не отвечает

1. Проверьте логи:
```bash
docker compose logs musicbot
```

2. Проверьте, что webhook установлен:
```bash
curl https://api.telegram.org/botВАШ_ТОКЕН/getWebhookInfo
```

3. Проверьте HEALTHCHECK:
```bash
curl http://localhost/healthz
```

### Нет доступа к VM по SSH

`scripts/ssh_port_monitor.py` — read-only диагностика с локального компьютера:
проверяет tcp/22 и пробует зайти по ключу, ничего не меняя на сервере.

```powershell
$env:SSH_MONITOR_HOST = "84.201.167.96"
$env:SSH_MONITOR_KEY  = "C:\путь\к\deploy_key"
python scripts/ssh_port_monitor.py
```

Хост, ключ, пользователь и путь к `ssh.exe` переопределяются переменными
`SSH_MONITOR_HOST` / `SSH_MONITOR_KEY` / `SSH_MONITOR_USER` / `SSH_MONITOR_EXE`.

### Ошибка 502 Bad Gateway

1. Проверьте, что musicbot контейнер запущен:
```bash
docker compose ps
```

2. Проверьте логи nginx:
```bash
docker compose logs nginx
```

### Нет доступа по HTTPS

1. Проверьте, что сертификаты на месте:
```bash
ls -la nginx/ssl/
```

2. Проверьте nginx конфиг:
```bash
docker compose exec nginx nginx -t
```

3. Перезапустите nginx:
```bash
docker compose restart nginx
```

### Проблемы с диском

1. Очистите старые логи:
```bash
find logs/ -name "*.log.*" -mtime +7 -delete
```

2. Проверьте размеры кэша и резервных копий, затем увеличьте диск при необходимости.

---

## 📝 Ресурсы хоста

Лимиты контейнеров заданы в `docker-compose.yml`; не снижайте их без измерений.
Ориентируйтесь на характеристики VM из начала документа, следите за `docker stats`
и нагрузкой на диск. Для существенного одновременного трафика сначала проведите
нагрузочный тест; масштабирование за пределы одной VM потребует отдельной настройки.

---

## 📞 Поддержка

Если возникли проблемы:
1. Проверьте логи: `docker compose logs -f`
2. Проверьте статус контейнеров: `docker compose ps`
3. Проверьте конфигурацию `.env`
4. Смотрите раздел Troubleshooting выше
