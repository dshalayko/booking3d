#!/usr/bin/env bash
# Запуск и обслуживание проекта локально.
#
#   ./run.sh            — поднять всё и запустить приложение
#   ./run.sh help       — список команд
#
# Скрипт идемпотентен: создаёт .env с секретами, venv и базу только если их
# ещё нет, и ничего не перезаписывает.

set -euo pipefail
cd "$(dirname "$0")"

VENV=.venv
PY="$VENV/bin/python"
PORT="${PORT:-8000}"

say() { printf "\033[1;34m→\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m✗\033[0m %s\n" "$*" >&2; exit 1; }

# --- подготовка --------------------------------------------------------------

ensure_env() {
    [ -f .env ] && return
    say "создаю .env и генерирую секреты"
    python3 - <<'PY'
import pathlib, secrets

example = pathlib.Path(".env.example").read_text()
password = secrets.token_hex(16)

lines = []
for line in example.splitlines():
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in {"SESSION_SECRET", "KIOSK_SECRET", "KIOSK_ENROLL_SECRET",
               "ADMIN_SECRET", "PIN_PEPPER"}:
        line = f"{key}={secrets.token_hex(32)}"
    elif key == "DB_PASSWORD":
        line = f"DB_PASSWORD={password}"
    elif key == "DATABASE_URL":
        line = f"DATABASE_URL=postgresql+psycopg://booking:{password}@localhost:5432/booking"
    lines.append(line)

pathlib.Path(".env").write_text("\n".join(lines) + "\n")
PY
    say "заполни TG_BOT_TOKEN в .env — без него бот не запустится (всё остальное работает)"
}

ensure_venv() {
    if [ ! -x "$PY" ]; then
        say "создаю виртуальное окружение"
        python3 -m venv "$VENV"
    fi
    if ! "$PY" -c "import fastapi" 2>/dev/null; then
        say "ставлю зависимости"
        "$VENV/bin/pip" install -q --upgrade pip
        "$VENV/bin/pip" install -q -r requirements-dev.txt
    fi
}

ensure_db() {
    docker info >/dev/null 2>&1 || die "Docker не запущен — открой Docker Desktop"
    docker compose up -d --wait db >/dev/null 2>&1 \
        || die "не удалось поднять Postgres, запусти вручную: docker compose up -d db"
}

secret_from_env() { grep "^$1=" .env | cut -d= -f2-; }

# --- команды -----------------------------------------------------------------

cmd_dev() {
    ensure_env; ensure_venv; ensure_db
    say "накатываю миграции"
    "$VENV/bin/alembic" upgrade head
    "$PY" -m app.cli seed_printers
    echo
    say "киоск:   http://127.0.0.1:$PORT/"
    say "админка: http://127.0.0.1:$PORT/admin/login"
    say "если форма PIN не появляется — ./run.sh urls и открой ссылку регистрации киоска"
    echo
    exec "$VENV/bin/uvicorn" app.main:app --reload --port "$PORT"
}

cmd_migrate() {
    ensure_venv; ensure_db
    "$VENV/bin/alembic" upgrade head
    "$PY" -m app.cli seed_printers
}

cmd_test() {
    ensure_venv; ensure_db
    "$PY" -m pytest "$@"
}

cmd_lint() {
    ensure_venv
    "$VENV/bin/ruff" check .
}

cmd_check() { cmd_lint && cmd_test; }

cmd_urls() {
    ensure_env
    echo "киоск:               http://127.0.0.1:$PORT/"
    echo "регистрация киоска:  http://127.0.0.1:$PORT/kiosk/enroll?secret=$(secret_from_env KIOSK_ENROLL_SECRET)"
    echo "админка:             http://127.0.0.1:$PORT/admin/login"
    echo "секрет админки:      $(secret_from_env ADMIN_SECRET)"
}

cmd_admin() {
    [ $# -eq 1 ] || die "нужен telegram chat id: ./run.sh admin 123456789"
    ensure_venv; ensure_db
    "$PY" -m app.cli make_admin "$1"
}

cmd_demo() {
    ensure_venv; ensure_db
    PYTHONPATH=. "$PY" - <<'PY'
import asyncio

from app.db import SessionLocal, engine
from app.models import User
from app.services.security import pin_digest

PEOPLE = [(900011, "i_petrov", "1111"), (900012, "a_kuznetsova", "2222")]


async def main() -> None:
    async with SessionLocal() as db:
        for chat_id, name, pin in PEOPLE:
            db.add(User(tg_chat_id=chat_id, name=name, pin_digest=pin_digest(pin)))
        await db.commit()
    await engine.dispose()
    for _, name, pin in PEOPLE:
        print(f"{name} — PIN {pin}")


asyncio.run(main())
PY
    say "теперь можно занять оба принтера и проверить очередь своим PIN"
}

cmd_reset() {
    ensure_db
    docker compose exec -T db psql -U booking booking -q -c "
        TRUNCATE sessions, queue RESTART IDENTITY;
        DELETE FROM users WHERE tg_chat_id IN (900011, 900012);
        UPDATE printers SET status='free', note=NULL;"
    say "состояние очищено, тестовые люди удалены"
}

cmd_fastforward() {
    ensure_db
    say "сдвигаю сроки в прошлое — сверка сработает в ближайшую минуту"
    docker compose exec -T db psql -U booking booking -q -c "
        UPDATE sessions SET eta_at = now() - interval '1 minute' WHERE status = 'printing';
        UPDATE queue SET offer_expires_at = now() - interval '1 minute' WHERE status = 'offered';"
}

cmd_psql() {
    ensure_db
    docker compose exec db psql -U booking booking
}

cmd_stop() {
    pkill -f "uvicorn app.main:app" 2>/dev/null && say "приложение остановлено" || true
    docker compose down
}

cmd_help() {
    cat <<'TEXT'
Команды:

  dev          поднять базу, накатить миграции и запустить приложение (по умолчанию)
  migrate      только миграции и сид принтеров
  test [...]   прогнать тесты (аргументы уходят в pytest)
  lint         ruff
  check        lint + test
  urls         ссылки на киоск, регистрацию киоска и админку
  admin <id>   выдать права админа по telegram chat id
  demo         создать двух тестовых людей с PIN 1111 и 2222
  fastforward  сдвинуть сроки печатей и предложений в прошлое
  reset        очистить состояние и удалить тестовых людей
  psql         консоль базы
  stop         остановить приложение и базу

Переменная PORT меняет порт (по умолчанию 8000).
TEXT
}

case "${1:-dev}" in
    dev|"")       cmd_dev ;;
    migrate)      cmd_migrate ;;
    test)         shift; cmd_test "$@" ;;
    lint)         cmd_lint ;;
    check)        cmd_check ;;
    urls)         cmd_urls ;;
    admin)        shift; cmd_admin "$@" ;;
    demo)         cmd_demo ;;
    fastforward)  cmd_fastforward ;;
    reset)        cmd_reset ;;
    psql)         cmd_psql ;;
    stop)         cmd_stop ;;
    help|-h|--help) cmd_help ;;
    *)            die "неизвестная команда: $1 (см. ./run.sh help)" ;;
esac
