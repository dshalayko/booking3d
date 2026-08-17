#!/usr/bin/env bash
# Обновление прода. Запускается на сервере, из каталога проекта.
#
#   ./deploy.sh          — подтянуть коммиты из origin и раскатить
#   ./deploy.sh --force  — пересобрать и перезапустить, даже если нового нет
#   ./deploy.sh --help
#
# Порядок такой: бэкап базы → git pull → пересборка тех контейнеров, которых
# касались изменения → проверка, что приложение действительно поднялось. Если не
# поднялось, печатает лог и готовую команду откката, а не оставляет догадываться.
#
# Миграции скрипт не запускает сам: их накатывает контейнер `app` при старте
# (см. command в docker-compose.prod.yml). Здесь только бэкап перед этим.
#
# Всё тело — в main() намеренно. Bash читает файл по мере выполнения, а скрипт
# обновляет сам себя тем же git pull: без обёртки замена файла на полпути ломала
# бы запуск. Правки в самом deploy.sh поэтому применяются со следующего раза.

set -euo pipefail

COMPOSE="docker compose -f docker-compose.prod.yml"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
BACKUP_KEEP_DAYS=14
HEALTH_URL="http://127.0.0.1:8000/healthz"
HEALTH_TIMEOUT=90

say()  { printf "\033[1;34m→\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m✗\033[0m %s\n" "$*" >&2; exit 1; }

usage() {
    # Верхний комментарий до пустой строки перед пояснением про main().
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
}

# --- проверки до того, как что-то менять -------------------------------------

preflight() {
    [ -f docker-compose.prod.yml ] || die "запускай из каталога проекта: docker-compose.prod.yml не найден"
    [ -f .env ] || die ".env не найден — сервер не настроен, см. DEPLOY.md"
    command -v docker >/dev/null || die "docker не установлен"
    docker info >/dev/null 2>&1 || die "docker не отвечает (нет прав? добавь себя в группу docker)"

    # Правки на сервере поверх git — обычно чья-то отладка «на живую». Молча
    # затирать их нельзя, а git pull всё равно упрётся в конфликт.
    if ! git diff --quiet || ! git diff --cached --quiet; then
        git --no-pager diff --stat HEAD
        die "в рабочем дереве есть незакоммиченные правки — разберись с ними до обновления"
    fi
}

# --- бэкап -------------------------------------------------------------------

backup_db() {
    mkdir -p "$BACKUP_DIR"
    local file="$BACKUP_DIR/booking_$(date +%F_%H%M%S).sql.gz"

    say "бэкап базы → $file"
    # pipefail поймает падение pg_dump: без него gzip вернул бы 0 на пустом входе.
    $COMPOSE exec -T db pg_dump -U booking booking | gzip > "$file"
    [ -s "$file" ] || die "бэкап вышел пустым — обновление прервано"
    ok "бэкап готов ($(du -h "$file" | cut -f1))"

    BACKUP_FILE="$file"
    find "$BACKUP_DIR" -name 'booking_*.sql.gz' -mtime +$BACKUP_KEEP_DAYS -delete
}

# --- что именно затронуто ----------------------------------------------------

# Заполняет CHANGED_* по диапазону коммитов: от этого зависит, что пересобирать.
detect_changes() {
    local range="$1" changed
    changed=$(git diff --name-only "$range")

    CHANGED_MIGRATIONS=$(grep -c '^migrations/versions/' <<<"$changed" || true)
    CHANGED_CADDY=$(grep -c '^Caddyfile$' <<<"$changed" || true)
    CHANGED_COMPOSE=$(grep -c '^docker-compose.prod.yml$' <<<"$changed" || true)
    CHANGED_ENV_EXAMPLE=$(grep -c '^\.env\.example$' <<<"$changed" || true)
    CHANGED_FRONT=$(grep -cE '^app/(static|templates)/' <<<"$changed" || true)
}

# Переменная, появившаяся в .env.example, но не дописанная в .env, роняет
# контейнер в цикл рестарта с «не задано в .env: …». Так уже было с PRINTER_NAMES.
check_env_keys() {
    local missing=()
    while read -r key; do
        [ -n "$key" ] || continue
        grep -q "^$key=" .env || missing+=("$key")
    done < <(grep -oE '^[A-Z_]+=' .env.example | tr -d '=')

    if [ ${#missing[@]} -gt 0 ]; then
        warn "в .env.example есть переменные, которых нет в твоём .env:"
        printf '    %s\n' "${missing[@]}"
        warn "если какая-то из них обязательна, app уйдёт в цикл рестарта — впиши и запусти снова"
    fi
}

# --- раскатка ----------------------------------------------------------------

restart_services() {
    if [ "$CHANGED_COMPOSE" -gt 0 ]; then
        say "менялся docker-compose.prod.yml — пересобираю всё"
        $COMPOSE up -d --build
    else
        say "пересобираю app"
        $COMPOSE up -d --build app
    fi

    # Caddyfile смонтирован отдельным файлом, а git pull заменяет его новым
    # inode: контейнер продолжает видеть старый, и caddy reload отвечает
    # «config is unchanged». Помогает только пересоздание. См. DEPLOY.md.
    if [ "$CHANGED_CADDY" -gt 0 ] && [ "$CHANGED_COMPOSE" -eq 0 ]; then
        say "менялся Caddyfile — пересоздаю caddy"
        $COMPOSE up -d --force-recreate caddy
    fi
}

wait_healthy() {
    say "жду, пока приложение ответит (до ${HEALTH_TIMEOUT}с)"
    local waited=0
    while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
        if curl -sf -o /dev/null --max-time 3 "$HEALTH_URL"; then
            ok "healthz отвечает (${waited}с)"
            return 0
        fi
        sleep 3
        waited=$((waited + 3))
    done
    return 1
}

rollback_hint() {
    local old="$1"
    printf '\n'
    warn "приложение не поднялось. Последние строки лога:"
    $COMPOSE logs --tail 30 app || true
    printf '\n'
    warn "откат кода:"
    printf '    git checkout %s && %s up -d --build app\n' "$old" "$COMPOSE"
    if [ -n "${BACKUP_FILE:-}" ]; then
        warn "если дело в миграции — восстановление базы из свежего бэкапа:"
        printf '    %s stop app\n' "$COMPOSE"
        printf '    gunzip -c %s | %s exec -T db psql -U booking -d booking\n' "$BACKUP_FILE" "$COMPOSE"
        printf '    %s start app\n' "$COMPOSE"
    fi
}

report() {
    local old="$1" new="$2"
    printf '\n'
    if [ "$old" = "$new" ]; then
        ok "пересобрано на $(git rev-parse --short "$new")"
    else
        ok "обновлено: $(git rev-parse --short "$old") → $(git rev-parse --short "$new")"
    fi

    local version
    version=$($COMPOSE exec -T db psql -U booking -d booking -tAc \
        'select version_num from alembic_version' 2>/dev/null | tr -d '\r' || true)
    [ -n "$version" ] && say "схема базы: $version"

    $COMPOSE ps --format 'table {{.Service}}\t{{.Status}}'

    if grep -q '^KIOSK_OPEN_ACCESS=true' .env; then
        printf '\n'
        warn "KIOSK_OPEN_ACCESS=true — PIN принимается с любого устройства. Это режим для тестов"
    fi
    if [ "$CHANGED_FRONT" -gt 0 ]; then
        say "менялись шаблоны или статика: планшеты обновятся сами в течение"
        say "десяти секунд — доска сверяет версию при каждом опросе (app/assets.py)"
    fi
}

# --- main --------------------------------------------------------------------

main() {
    local force=0
    case "${1:-}" in
        --help|-h) usage; exit 0 ;;
        --force)   force=1 ;;
        "")        ;;
        *)         die "неизвестный аргумент: $1 (см. --help)" ;;
    esac

    cd "$(dirname "$0")"
    preflight

    say "смотрю, что нового в origin"
    git fetch --quiet origin

    local old new
    old=$(git rev-parse HEAD)
    new=$(git rev-parse origin/main)

    if [ "$old" = "$new" ]; then
        if [ "$force" -eq 0 ]; then
            ok "уже на последнем коммите $(git rev-parse --short HEAD) — нечего обновлять"
            say "пересобрать всё равно: ./deploy.sh --force"
            exit 0
        fi
        warn "нового в origin нет, но --force: пересобираю текущий коммит"
    else
        printf '\n'
        say "к раскатке:"
        git --no-pager log --oneline "$old..$new" | sed 's/^/    /'
        printf '\n'
    fi

    detect_changes "$old..$new"
    [ "$CHANGED_MIGRATIONS" -gt 0 ] && say "в обновлении есть миграции — они накатятся при старте app"

    backup_db

    if [ "$old" != "$new" ]; then
        # ff-only: если история разошлась (кто-то коммитил на сервере), лучше
        # честно упасть, чем создать merge-коммит на проде.
        say "подтягиваю код"
        git pull --ff-only --quiet origin main
    fi

    [ "$CHANGED_ENV_EXAMPLE" -gt 0 ] && check_env_keys

    restart_services

    if ! wait_healthy; then
        rollback_hint "$old"
        exit 1
    fi

    # Пересборки оставляют висячие образы, а диск на VPS не бесконечный.
    docker image prune -f >/dev/null 2>&1 || true

    report "$old" "$new"
}

main "$@"
