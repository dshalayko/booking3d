# Развёртывание на VPS

Инструкция для варианта «приложение на арендованном сервере». Всё крутится в
Docker: три контейнера — Postgres, приложение, Caddy как reverse proxy с
автоматическим HTTPS.

План работ по самой системе — в [PLAN.md](PLAN.md).

> **Главное следствие выбора VPS:** при обрыве интернета в коворкинге iPad на
> стене перестаёт работать целиком, а не только уведомления. Поэтому в шаге 5
> плана есть офлайн-заглушка — экран должен объяснить человеку, что произошло.

---

## Содержание

- [Что понадобится](#что-понадобится)
- [1. Сервер и домен](#1-сервер-и-домен)
- [2. Настройка сервера](#2-настройка-сервера)
- [3. Файлы проекта](#3-файлы-проекта)
- [4. Первый запуск](#4-первый-запуск)
- [5. Настройка iPad](#5-настройка-ipad)
- [6. Бэкапы](#6-бэкапы)
- [7. Обновление и откат](#7-обновление-и-откат)
- [8. Эксплуатация](#8-эксплуатация)
- [Чек-лист безопасности](#чек-лист-безопасности)
- [Типовые проблемы](#типовые-проблемы)

---

## Что понадобится

| Что | Требование |
|---|---|
| VPS | 2 vCPU / 2 ГБ RAM / 20 ГБ SSD, Ubuntu 24.04 LTS. ~5–7 € в месяц |
| Домен | реальный, например `printers.example.com`. Let's Encrypt не выдаёт сертификаты на голый IP |
| Telegram-бот | токен от [@BotFather](https://t.me/BotFather) |
| Хранилище бэкапов | S3-совместимое или любой удалённый диск, куда достучится `rclone` |

Провайдера бери географически ближе к коворкингу — киоск обновляется каждые
10 секунд, лишние 200 мс задержки заметны на глаз.

---

## 1. Сервер и домен

1. Создай VPS на Ubuntu 24.04, залей свой SSH-ключ при создании.
2. Пропиши A-запись `printers.example.com` → IP сервера.
3. Дождись распространения DNS — Caddy не получит сертификат, пока имя не
   резолвится:

```bash
dig +short printers.example.com
```

Пока эта команда не вернёт IP сервера, дальше идти бессмысленно.

---

## 2. Настройка сервера

Всё под root при первом входе.

### Пользователь и SSH

Если образ провайдера уже отдаёт непривилегированного пользователя с sudo и
твоим ключом (на облачных образах Ubuntu это `ubuntu`) — своего создавать не
надо, работай под ним, и дальше читай `/home/booking/booking` как
`/home/<пользователь>/booking`. Иначе:

```bash
adduser booking
usermod -aG sudo booking
rsync --archive --chown=booking:booking ~/.ssh /home/booking
```

Отключаем вход по паролю и по root. На Ubuntu 24.04 настройки из
`sshd_config.d` перекрывают основной файл, поэтому пишем именно туда:

```bash
cat > /etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
systemctl restart ssh
```

**Не закрывай текущую сессию, пока не проверишь вход в новом окне** как
`booking@printers.example.com`. Иначе рискуешь запереть себя снаружи.

### Firewall

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

Здесь есть известная ловушка: **Docker пишет свои правила iptables в обход ufw**,
и порт, опубликованный через `ports:`, окажется доступен из интернета, даже если
ufw его не разрешал. В нашем compose это учтено: наружу публикует порты только
Caddy (80/443 — они и так открыты), Postgres не публикует ничего, а приложение
привязано к `127.0.0.1`. Если будешь добавлять сервисы — держи это в голове.

### Swap

Нужен, если у сервера меньше 2 ГБ RAM: `pip install` при сборке образа —
самый тяжёлый момент за всё время жизни системы, и на 1 ГБ он падает по OOM,
а без swap следом за ним может умереть и Postgres.

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

Вторая строка нужна, чтобы swap вернулся после перезагрузки.

### Автообновления безопасности

```bash
apt update && apt install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades
```

### Docker

```bash
curl -fsSL https://get.docker.com | sh
usermod -aG docker booking
```

Дальше — под пользователем `booking`.

---

## 3. Файлы проекта

```bash
cd ~
git clone https://github.com/dshalayko/booking3d.git booking
cd booking
```

### `Dockerfile`

```dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Драйвер БД ставь как `psycopg[binary]` — тогда в образе не нужны ни `gcc`, ни
`libpq-dev`, и он остаётся маленьким.

### `docker-compose.prod.yml`

```yaml
x-logging: &logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"

services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: booking
      POSTGRES_USER: booking
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      TZ: ${TZ}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U booking"]
      interval: 10s
      timeout: 5s
      retries: 5
    logging: *logging

  app:
    build: .
    restart: unless-stopped
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
    command: >
      sh -c "alembic upgrade head &&
             uvicorn app.main:app --host 0.0.0.0 --port 8000"
    ports:
      - "127.0.0.1:8000:8000"   # только для SSH-туннеля к админке
    logging: *logging

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    environment:
      SITE_DOMAIN: "${SITE_DOMAIN:?не задано в .env — SITE_DOMAIN}"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - app
    logging: *logging

volumes:
  pgdata:
  caddy_data:
  caddy_config:
```

Три вещи, которые тут делают работу:

- `restart: unless-stopped` — после перезагрузки сервера всё поднимается само;
- миграции в `command` — при каждом деплое схема догоняется без ручного шага;
- `ports: 127.0.0.1:8000` — приложение доступно с самого сервера, но не из
  интернета; так админка открывается через SSH-туннель.

### `Caddyfile`

```
{$SITE_DOMAIN} {
    encode gzip
    reverse_proxy app:8000

    # админка недоступна из интернета — только через SSH-туннель
    @admin path /admin*
    respond @admin 404

    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "same-origin"
    }
}
```

Домен подставляется из `SITE_DOMAIN` в `.env`, сам файл править не нужно —
иначе на сервере каждый `git pull` конфликтовал бы с локальной правкой.

Сертификат Caddy получит и будет продлевать сам, ничего настраивать не нужно.

### `.env`

```bash
cp .env.example .env
```

```ini
TZ=Europe/Nicosia
PUBLIC_BASE_URL=https://printers.example.com
SITE_DOMAIN=printers.example.com

UI_LANG=ru
PRINTER_NAMES="P2S #1,P2S #2"

DB_PASSWORD=<openssl rand -hex 24>
DATABASE_URL=postgresql+psycopg://booking:<тот же пароль>@db:5432/booking

TG_BOT_TOKEN=<токен от BotFather>

SESSION_SECRET=<openssl rand -hex 32>
KIOSK_SECRET=<openssl rand -hex 32>
KIOSK_ENROLL_SECRET=<openssl rand -hex 32>
ADMIN_SECRET=<openssl rand -hex 32>
PIN_PEPPER=<openssl rand -hex 32>

OFFER_WINDOW_MINUTES=30
NIGHT_START=23:00
NIGHT_END=08:00
UNCLAIMED_PING_MINUTES=60
```

Секреты генерируй так:

```bash
openssl rand -hex 32
```

`TZ` поставь свой — по нему считается ночная пауза очереди 23:00–08:00. Ошибка в
часовом поясе проявится не сразу, а в виде «предложение сгорело ночью, хотя не
должно было».

`PRINTER_NAMES` — имена принтеров так, как они подписаны на стене. Дефолта в
коде нет намеренно: без этой строки приложение не поднимется и скажет, чего не
хватает. **Если обновляешь развёрнутый сервер**, у которого `.env` собран до
появления этой переменной — допиши её перед перезапуском, иначе контейнер `app`
уйдёт в перезапуск с `не задано в .env: PRINTER_NAMES`. Кавычки обязательны:
`#` без них dotenv считает началом комментария.

Имена уже созданных принтеров живут в БД, поэтому правка `PRINTER_NAMES` их не
переименует — для этого `docker compose exec app python -m app.cli
rename_printer <id> "<новое имя>"`.

Секреты после запуска не меняй без нужды, у каждого своя цена ротации:
`KIOSK_SECRET` — придётся заново регистрировать iPad, `SESSION_SECRET` —
разлогинятся все, `PIN_PEPPER` — все PIN-ы перестанут работать и их надо выдать
заново через бота.

Файл `.env` в git не коммитится. Убедись, что он в `.gitignore`.

---

## 4. Первый запуск

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Проверка:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f app
curl -I https://printers.example.com
```

Сид принтеров и админ:

```bash
docker compose -f docker-compose.prod.yml exec app python -m app.cli seed_printers
docker compose -f docker-compose.prod.yml exec app python -m app.cli make_admin <твой_tg_id>
```

Свой `tg_id` узнаёшь у [@userinfobot](https://t.me/userinfobot) или в логах после
`/start` своему боту.

Админка — через туннель с ноутбука:

```bash
ssh -L 8000:localhost:8000 booking@printers.example.com
```

Дальше `http://localhost:8000/admin` в браузере, вход по `ADMIN_SECRET`.

---

## 5. Настройка iPad

Порядок важен: сначала регистрируем планшет как киоск, потом закрепляем на стене.

1. Открой в Safari на iPad:
   `https://printers.example.com/kiosk/enroll?secret=<KIOSK_ENROLL_SECRET>`
   Ставится device-cookie на 10 лет, происходит редирект на главный экран. Без
   неё форма ввода PIN на этом устройстве не появится.
2. **Поделиться → На экран «Домой»** — сайт становится PWA без адресной строки.
3. **Настройки → Экран и яркость → Автоблокировка → Никогда.**
4. **Настройки → Универсальный доступ → Гид-доступ → включить.** Затем открой
   приложение и тройное нажатие боковой кнопки — экран заблокирован на одной
   странице, уйти в Safari или свернуть нельзя.
5. Постоянное питание. iPad на стене без зарядки живёт максимум сутки.
6. Проверь весь цикл прямо с планшета: занять → освободить → встать в очередь.

Если планшет придётся сбросить или заменить — просто повтори шаг 1 на новом
устройстве. Старую device-cookie этим не отзовёшь; чтобы отозвать все сразу,
меняй `KIOSK_SECRET` в `.env` и перезапускай `app`.

---

## 6. Бэкапы

База крошечная, дамп — сотни килобайт. Дорого стоит не место, а его отсутствие.

`/home/booking/backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR=/home/booking/backups
COMPOSE="docker compose -f /home/booking/booking/docker-compose.prod.yml"
STAMP=$(date +%F_%H%M)

mkdir -p "$BACKUP_DIR"
$COMPOSE exec -T db pg_dump -U booking booking | gzip > "$BACKUP_DIR/booking_$STAMP.sql.gz"

# копия за пределы сервера — обязательна, иначе бэкап умрёт вместе с VPS
rclone copy "$BACKUP_DIR/booking_$STAMP.sql.gz" remote:booking-backups

find "$BACKUP_DIR" -name 'booking_*.sql.gz' -mtime +14 -delete
```

```bash
chmod +x /home/booking/backup.sh
crontab -e
```

```
0 4 * * * /home/booking/backup.sh >> /home/booking/backup.log 2>&1
```

**Восстановление** (проверь его один раз до того, как понадобится):

```bash
docker compose -f docker-compose.prod.yml stop app
gunzip -c backups/booking_2026-08-10_0400.sql.gz \
  | docker compose -f docker-compose.prod.yml exec -T db psql -U booking -d booking
docker compose -f docker-compose.prod.yml start app
```

Если восстанавливаешь поверх непустой базы, сначала пересоздай её:
`dropdb`/`createdb` внутри контейнера db.

Снапшоты диска у провайдера включи тоже — они спасают от «сервер не загрузился»,
чего `pg_dump` не покрывает.

---

## 7. Обновление и откат

```bash
cd /home/booking/booking
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

Миграции применятся при старте, простой — около 10 секунд. Перед обновлением с
миграциями сделай бэкап вручную (`./backup.sh`).

**Откат кода:**

```bash
git checkout <предыдущий-коммит>
docker compose -f docker-compose.prod.yml up -d --build
```

**Откат миграции** (только если новая версия сломала схему):

```bash
docker compose -f docker-compose.prod.yml exec app alembic downgrade -1
```

`downgrade` может потерять данные, добавленные новой схемой. Если сомневаешься —
восстанавливай из бэкапа, а не откатывай миграцию.

---

## 8. Эксплуатация

**Логи:**

```bash
docker compose -f docker-compose.prod.yml logs -f app
docker compose -f docker-compose.prod.yml logs --tail 100 caddy
```

Ротация настроена в compose (10 МБ × 3 файла на сервис), диск не забьётся.

**Состояние:**

```bash
docker compose -f docker-compose.prod.yml ps
docker stats --no-stream
df -h
```

**Мониторинг.** Сервер не может сообщить о собственной смерти, поэтому нужны два
уровня:

1. Бот пишет админу в личку при старте приложения. Пришло «система запустилась»
   в 4 утра — значит что-то падало и перезапускалось.
2. Внешняя проверка `https://printers.example.com/healthz` раз в 5 минут —
   healthchecks.io, UptimeRobot или любой аналог. Это единственное, что заметит
   полное падение VPS.

**Консоль БД:**

```bash
docker compose -f docker-compose.prod.yml exec db psql -U booking booking
```

---

## Чек-лист безопасности

- [ ] Вход по SSH только по ключу, пароль и root отключены, проверено вторым окном
- [ ] `ufw` включён, наружу торчат только 22, 80, 443
- [ ] Postgres не публикует портов, приложение слушает `127.0.0.1`
- [ ] `/admin` отдаёт 404 из интернета, доступна через SSH-туннель
- [ ] Все секреты в `.env` сгенерированы `openssl rand`, файл не в git
- [ ] PIN принимается только с устройства с device-cookie
- [ ] Рейт-лимит на POST-действия киоска (там вводится PIN) работает
- [ ] `robots.txt` с `Disallow: /`
- [ ] `unattended-upgrades` включён
- [ ] Бэкап отработал хотя бы раз, и восстановление проверено

---

## Типовые проблемы

**Caddy не выдаёт сертификат.** Смотри `logs caddy`. Причины по частоте: DNS ещё
не распространился, 80-й порт закрыт в firewall провайдера (не только в ufw),
домен указывает на другой IP. HTTP-01 требует доступности порта 80 снаружи.

**Бот не отвечает или падает с `Conflict: terminated by other getUpdates`.** С
одним токеном может работать только один процесс long polling. Значит где-то
поднят второй инстанс — обычно локальная разработка на том же токене. Заведи
отдельного тестового бота для дева.

**Контейнер `app` в цикле рестарта.** Почти всегда упала миграция или неверный
`DATABASE_URL`. `logs app` покажет причину. Пока не починишь, приложение будет
перезапускаться каждые несколько секунд.

**Ночная пауза очереди срабатывает не в те часы.** Проверь `TZ` — он должен быть
задан и для `app`, и для `db`. `date` внутри контейнера покажет фактическое
время.

**Кончилось место на диске.** `docker system prune -af --volumes` удалит мусор,
но `--volumes` снесёт и том с базой, если он не используется запущенным
контейнером. Безопаснее без этого флага: `docker system prune -af`.

**Напоминания не приходят.** Планировщик не хранит отложенных заданий: раз в
минуту он сверяет состояние в БД с часами. В логах должна быть строка
`сверка: предупреждено … завершено …` — если её нет, планировщик не стартовал,
смотри `logs app`. Если строка есть, а сообщения не доходят — ищи рядом
`не удалось отправить сообщение в <chat_id>`: человек заблокировал бота.

**Принтер завис в статусе «печатает» после долгого простоя.** Так быть не должно:
первая же сверка после запуска догоняет пропущенное. Проверь `TZ` и что
контейнер `app` действительно поднялся.
