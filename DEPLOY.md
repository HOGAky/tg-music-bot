# Деплой tg-music-bot на Yandex Cloud

## 📋 Требования

### Необходимые данные для деплоя:

1. **Telegram Bot Token** - от @BotFather
2. **SSH доступ к VM** - публичный IP адрес и SSH ключ
3. **Домен (опционально)** - для HTTPS и Web App
4. **SSL сертификат (опционально)** - Lets Encrypt или собственный

### Характеристики VM:
- 2 vCPU
- 2 GB RAM
- 15 GB диск
- Ubuntu 22.04 LTS (рекомендуется)

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
WEBHOOK_ENABLED=true
WEBHOOK_URL=https://ваш-домен или http://ваш-ip
WEBHOOK_PATH=/webhook
WEBAPP_URL=https://ваш-домен/app/  # если используете Web App
API_ENABLED=true
```

Опциональные переменные:
```bash
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
YANDEX_TOKEN=
LOG_LEVEL=INFO
LOG_FORMAT=text
```

#### 1.3. Тестовая сборка локально (опционально)

```bash
docker-compose build
docker-compose up
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
sudo apt install docker-compose -y
```

#### 3.4. Добавить пользователя в docker группу

```bash
sudo usermod -aG docker $USER
```

Выйти и зайти снова для применения изменений.

#### 3.5. Проверить установку

```bash
docker --version
docker-compose --version
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

### Шаг 5: Получение SSL сертификата (опционально)

Если у вас есть домен, рекомендуется использовать Lets Encrypt.

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
sudo cp /etc/letsencrypt/live/ваш-домен.com/fullchain.pem ~/musicbot/nginx/ssl/
sudo cp /etc/letsencrypt/live/ваш-домен.com/privkey.pem ~/musicbot/nginx/ssl/
sudo chown $USER:$USER ~/musicbot/nginx/ssl/*.pem
```

#### 5.4. Настроить автообновление

```bash
sudo crontab -e
```

Добавить строку:
```
0 3 * * * certbot renew --quiet && cp /etc/letsencrypt/live/ваш-домен.com/fullchain.pem ~/musicbot/nginx/ssl/ && cp /etc/letsencrypt/live/ваш-домен.com/privkey.pem ~/musicbot/nginx/ssl/ && docker-compose -f ~/musicbot/docker-compose.yml restart nginx
```

---

### Шаг 6: Настройка nginx для HTTPS (если есть SSL)

#### 6.1. Раскомментировать HTTPS секцию в nginx конфиге

```bash
nano nginx/nginx.conf
```

Найдите секцию с `listen 443 ssl` и раскомментируйте её.

Замените `your-domain.com` на ваш домен.

#### 6.2. Раскомментировать перенаправление HTTP на HTTPS

В секции с `listen 80` раскомментируйте:
```nginx
return 301 https://$host$request_uri;
```

---

### Шаг 7: Запуск контейнеров

#### 7.1. Собрать образы

```bash
docker-compose build
```

#### 7.2. Запустить в фоне

```bash
docker-compose up -d
```

#### 7.3. Проверить статус

```bash
docker-compose ps
```

#### 7.4. Посмотреть логи

```bash
# Все логи
docker-compose logs -f

# Логи только бота
docker-compose logs -f musicbot

# Логи nginx
docker-compose logs -f nginx
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

#### 8.4. Проверить Web App (если включён)

- Нажмите на кнопку меню "🎵 Плеер"
- Должен открыться веб-плеер

---

## 🔧 Управление

### Просмотр логов

```bash
docker-compose logs -f musicbot
```

### Перезапуск бота

```bash
docker-compose restart musicbot
```

### Перезапуск nginx

```bash
docker-compose restart nginx
```

### Остановка всех контейнеров

```bash
docker-compose down
```

### Остановка с удалением volumes

```bash
docker-compose down -v
```

### Обновление проекта

```bash
# 1. Залить новые файлы (через git или scp/rsync)
git pull  # или scp/rsync

# 2. Пересобрать и перезапустить
docker-compose build
docker-compose up -d
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
docker-compose logs musicbot
```

2. Проверьте, что webhook установлен:
```bash
curl https://api.telegram.org/botВАШ_ТОКЕН/getWebhookInfo
```

3. Проверьте HEALTHCHECK:
```bash
curl http://localhost/healthz
```

### Ошибка 502 Bad Gateway

1. Проверьте, что musicbot контейнер запущен:
```bash
docker-compose ps
```

2. Проверьте логи nginx:
```bash
docker-compose logs nginx
```

### Нет доступа по HTTPS

1. Проверьте, что сертификаты на месте:
```bash
ls -la nginx/ssl/
```

2. Проверьте nginx конфиг:
```bash
docker-compose exec nginx nginx -t
```

3. Перезапустите nginx:
```bash
docker-compose restart nginx
```

### Проблемы с диском (15GB может закончиться)

1. Очистите старые логи:
```bash
find logs/ -name "*.log.*" -mtime +7 -delete
```

2. Очистите кэш (опционально):
```bash
rm -rf downloads/*
```

3. Увеличьте диск в Yandex Cloud Console.

---

## 📝 Оптимизация для 2GB RAM

С учётом ограничений VM (2GB RAM):

1. **Ограничить память контейнеров** в `docker-compose.yml`:
```yaml
services:
  musicbot:
    mem_limit: 1g
  nginx:
    mem_limit: 256m
```

2. **Уменьшить MAX_CONCURRENT_DOWNLOADS** в `.env`:
```bash
MAX_CONCURRENT_DOWNLOADS=1
```

3. **Использовать swap**:
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 🔄 Автоматический деплой (опционально)

### Настроить webhook для автоматического деплоя при пуше в git

1. Создать скрипт `deploy.sh` на VM:
```bash
#!/bin/bash
cd ~/musicbot
git pull
docker-compose build
docker-compose up -d
```

2. Сделать исполняемым:
```bash
chmod +x deploy.sh
```

3. Настроить GitHub webhook для trigger деплоя (требует дополнительного сервера).

---

## 📞 Поддержка

Если возникли проблемы:
1. Проверьте логи: `docker-compose logs -f`
2. Проверьте статус контейнеров: `docker-compose ps`
3. Проверьте конфигурацию `.env`
4. Смотрите раздел Troubleshooting выше
