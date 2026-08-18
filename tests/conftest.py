"""Тесты гоняются на настоящем Postgres.

Правила 1 и 2 живут в частичных уникальных индексах, а занятие принтера — в
`SELECT ... FOR UPDATE`. На SQLite или на моках ни то, ни другое не проверить,
поэтому conftest поднимает отдельную базу `booking_test` и накатывает на неё
миграции той же командой, что и прод.
"""

import os

# Набор проверяет формулировки дословно («уже занят», «Свободен»), то есть
# привязан к русскому. Язык фиксируется здесь, до импорта приложения: иначе
# `UI_LANG=en` в чьём-нибудь .env роняет половину тестов, не найдя ни одной
# настоящей ошибки. Совпадение языков между собой проверяет test_texts.py.
os.environ["UI_LANG"] = "ru"

import itertools
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.deps import get_db
from app.api.render import templates
from app.config import settings
from app.enums import MachineKind, MachineStatus, RoomKind
from app.main import app
from app.models import Machine, Room, User
from app.services.security import pin_digest

TEST_DB_NAME = "booking_test"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# `rooms` вычищается вместе с остальными, а CASCADE забирает и `work_hours`:
# помещения заводят сами тесты, и оставшаяся от миграции комната мешала бы
# считать их — в списке помещений она была бы третьей лишней.
TABLES = "booking_policy, users, rooms, machines, sessions, queue, reservations"

TEST_ZONE = ZoneInfo("Europe/Nicosia")


@pytest.fixture(autouse=True)
def fixed_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пины параметров, которые иначе прилетают из чужого `.env`.

    Без них результат зависел бы от `.env` того, кто запускает тесты: сменил
    человек `TZ` — и проверки расписания начинают падать на ровном месте.

    `kiosk_open_access` здесь по той же причине, но цена ошибки выше: с ним
    включённым правило 11 не проверяется вовсе, и тесты о том, что PIN нельзя
    ввести с чужого устройства, зеленели бы, ничего не проверяя. Кто включил
    флаг у себя для прогона цикла — не должен об этом узнавать через два
    падения в наборе.
    """
    monkeypatch.setattr(settings, "kiosk_open_access", False)
    # По той же причине, что и строка выше, но цена ошибки больше: с включённым
    # `miniapp_open_access` проверка подписи Telegram не выполняется вовсе, и
    # весь test_miniapp.py зеленел бы, ничего не проверяя.
    monkeypatch.setattr(settings, "miniapp_open_access", False)
    # QR под клавиатурой собирается на старте из `TG_BOT_USERNAME` (app/qr.py).
    # Без пина чужой .env решал бы, что стоит под клавиатурой — кнопка с кодом
    # или прежняя строчка про /start. Тест на саму кнопку включает имя явно.
    monkeypatch.setattr(settings, "tg_bot_username", "")
    monkeypatch.setitem(templates.env.globals, "bot_username", "")
    monkeypatch.setitem(templates.env.globals, "bot_qr", "")
    monkeypatch.setattr(settings, "tz", "Europe/Nicosia")
    monkeypatch.setattr(settings, "zone", TEST_ZONE)
    monkeypatch.setattr(settings, "night_start", time(23, 0))
    monkeypatch.setattr(settings, "night_end", time(8, 0))
    monkeypatch.setattr(settings, "offer_window_minutes", 30)
    monkeypatch.setattr(settings, "warn_before_minutes", 15)
    monkeypatch.setattr(settings, "unclaimed_ping_minutes", 60)
    monkeypatch.setattr(settings, "night_until", time(9, 0))
    monkeypatch.setattr(settings, "reservation_horizon_days", 14)
    monkeypatch.setattr(settings, "reservation_slot_minutes", 60)
    monkeypatch.setattr(settings, "reservation_min_minutes", 60)
    monkeypatch.setattr(settings, "reservation_grace_minutes", 30)
    monkeypatch.setattr(settings, "reservation_remind_minutes", 60)


@pytest.fixture
def work_slot() -> Callable[..., datetime]:
    """Начало брони в рабочий час — по умолчанию завтра в 10:00 местного времени.

    Не `align(now) + сутки`, как было раньше: бронировать можно только рабочие
    часы (08:00–20:00 по умолчанию), и прогон набора в семь утра или в полночь
    получал бы отказ вместо формы бронирования. Само правило проверяет
    tests/test_workhours.py — там часы задаются явно.

    Час всё-таки от настоящих часов, а не от фиксированной даты: экраны зовут
    `datetime.now`, и подделать его на весь запрос нечем.
    """

    def _slot(hour: int = 10, days: int = 1) -> datetime:
        moment = datetime.now(TEST_ZONE) + timedelta(days=days)
        return moment.replace(hour=hour, minute=0, second=0, microsecond=0).astimezone(UTC)

    return _slot


def _url_for(database: str) -> URL:
    return make_url(settings.database_url).set(database=database)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Пересоздать тестовую базу и накатить миграции."""
    admin_dsn = _url_for("postgres").set(drivername="postgresql").render_as_string(False)
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')

    url = _url_for(TEST_DB_NAME).render_as_string(False)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
    )
    return url


@pytest_asyncio.fixture
async def engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(test_database_url)
    async with engine.begin() as conn:
        # Часы работы уезжают вместе с помещениями (ON DELETE CASCADE): комнату
        # без своей строки часов обслуживает `workhours.DEFAULT` — те же
        # 08:00–20:00, на которые рассчитаны проверки расписания.
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Фабрика независимых сессий — нужна тесту на гонку."""
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessions() as session:
        yield session


@pytest_asyncio.fixture
async def room(db: AsyncSession) -> Room:
    """Мастерская, в которой стоит всё остальное.

    Помещение задаёт состав парка и часы работы, поэтому без него не существует
    машины: `room_id` у неё не nullable.
    """
    item = Room(name="Мастерская", kind=RoomKind.WORKSHOP)
    db.add(item)
    await db.commit()
    return item


@pytest_asyncio.fixture
async def other_room(db: AsyncSession) -> Room:
    """Вторая мастерская — ею проверяются переходы между помещениями.

    Отдельной фикстурой, а не внутри `room`: большинство проверок про одно
    помещение, и лишняя комната в них только мешала бы считать список.
    """
    item = Room(name="Мастерская на первом", kind=RoomKind.WORKSHOP)
    db.add(item)
    await db.commit()
    return item


@pytest_asyncio.fixture
async def printers(db: AsyncSession, room: Room) -> list[Machine]:
    """Два одинаковых принтера, как в коворкинге.

    Имена заданы здесь, а не из `PRINTER_NAMES`: тесты сверяют их дословно и не
    должны зависеть от чужого .env — по той же причине, что и `UI_LANG` выше.
    Разбор самой переменной проверяет test_machines.py.
    """
    items = [
        Machine(
            room_id=room.id, name="P2S #1", kind=MachineKind.PRINTER, status=MachineStatus.FREE
        ),
        Machine(
            room_id=room.id, name="P2S #2", kind=MachineKind.PRINTER, status=MachineStatus.FREE
        ),
    ]
    db.add_all(items)
    await db.commit()
    return items


@pytest_asyncio.fixture
async def engravers(db: AsyncSession, room: Room) -> list[Machine]:
    """Гравировщик рядом с принтерами — парк из машин разного типа.

    Отдельной фикстурой, а не внутри `printers`: большинство проверок про
    принтеры, и лишняя машина в них только мешала бы считать свободные.
    """
    items = [
        Machine(
            room_id=room.id,
            name="Гравёр #1",
            kind=MachineKind.ENGRAVER,
            status=MachineStatus.FREE,
        )
    ]
    db.add_all(items)
    await db.commit()
    return items


@pytest_asyncio.fixture
async def meeting(db: AsyncSession) -> tuple[Room, Machine]:
    """Переговорная: помещение вместе со своей единицей брони.

    Так её и заводит `services/rooms.create` — комната и строка в `machines` с
    тем же именем. Здесь пара собирается руками, чтобы фикстура не зависела от
    прав админа.
    """
    item = Room(name="Переговорная «Дуб»", kind=RoomKind.MEETING)
    db.add(item)
    await db.commit()
    unit = Machine(
        room_id=item.id,
        name=item.name,
        kind=MachineKind.MEETING_ROOM,
        status=MachineStatus.FREE,
    )
    db.add(unit)
    await db.commit()
    return item, unit


@pytest_asyncio.fixture
async def client(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """HTTP-клиент поверх приложения с подменённой сессией БД на тестовую."""

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(db: AsyncSession) -> Callable[..., Awaitable[User]]:
    counter = itertools.count(1)

    async def _make(
        name: str | None = None, is_admin: bool = False, pin: str | None = None
    ) -> User:
        number = next(counter)
        user = User(
            tg_chat_id=1000 + number,
            name=name or f"Человек {number}",
            pin_digest=pin_digest(pin or f"{number:04d}"),
            is_admin=is_admin,
        )
        db.add(user)
        await db.commit()
        return user

    return _make
