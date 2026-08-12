"""Служебные команды.

    python -m app.cli seed_printers
    python -m app.cli machines
    python -m app.cli rename_machine <id-или-имя> <новое имя>
    python -m app.cli add_machine <имя> [--kind printer|engraver]
    python -m app.cli remove_machine <id-или-имя>
    python -m app.cli make_admin <tg_chat_id> [--name Имя]

Состав парка теперь правится из админки, вкладка «Оборудование» — эти команды
остались для случая, когда админка недоступна: первый запуск на пустой базе,
разбор на месте по SSH. Логика у них та же самая, из `services/machines.py`,
поэтому «нельзя удалить машину с историей» здесь и там значит одно и то же.

Про `seed_printers` и `PRINTER_NAMES`. Переменная нужна ровно один раз — чтобы
на свежей базе появились принтеры и было что показать на стене. Дальше парк
живёт в таблице `machines`: `seed_printers` только досоздаёт недостающее по
имени и ничего не удаляет. Гравировщиков она не касается вовсе — их заводят
руками, из админки или `add_machine`.
"""

import argparse
import asyncio

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, engine
from app.enums import MachineKind, MachineStatus
from app.models import Machine, User
from app.services import machines as machines_svc
from app.services.auth import pick_free_pin
from app.services.errors import DomainError
from app.services.security import pin_digest


async def _cli_admin(db) -> User:
    """Кем представляются команды доменным функциям.

    Права проверяет сервис, а у командной строки нет учётной записи — берём
    первого админа, как это делает и админка. Если админов нет, значит база
    свежая: заведи админа (`make_admin`) или пользуйся `seed_printers`.
    """
    admin = await db.scalar(select(User).where(User.is_admin.is_(True)).order_by(User.id))
    if admin is None:
        raise RuntimeError(
            "в базе нет ни одного админа — заведи: python -m app.cli make_admin <tg_chat_id>"
        )
    return admin


async def seed_printers() -> None:
    """Создать принтеры, объявленные в PRINTER_NAMES. Существующие не трогает."""
    async with SessionLocal() as db:
        printers = await machines_svc.list_machines(db, kind=MachineKind.PRINTER)
        existing = {printer.name for printer in printers}
        declared = set(settings.printers)
        unknown = existing - declared  # есть в базе, нет в .env
        missing = declared - existing  # есть в .env, нет в базе

        # Одновременно и лишние, и недостающие — это почти всегда
        # переименование: имя в .env поправили, а сид сопоставляет по имени и
        # завёл бы второй парк рядом с первым. История печатей осталась бы на
        # старых строках, а на стене появились бы четыре плитки вместо двух.
        # Догадываться, какое старое имя соответствует какому новому, нельзя —
        # поэтому отказываемся и просим сказать это явно.
        if unknown and missing:
            print(
                "не буду сидить: похоже на переименование, а не на новый парк.\n\n"
                f"  в базе, но не в PRINTER_NAMES: {', '.join(sorted(unknown))}\n"
                f"  в PRINTER_NAMES, но не в базе: {', '.join(sorted(missing))}\n\n"
                "если это переименование — сделай его явно, история останется на месте:\n"
                "  админка → «Оборудование» → «Переименовать»\n"
                '  или: python -m app.cli rename_machine <id> "<новое имя>"\n\n'
                "если машины правда новые — заведи их по одной "
                "(add_machine), а уехавшие выведи в обслуживание из админки."
            )
            return

        for name in settings.printers:
            if name in existing:
                print(f"уже есть: {name}")
                continue
            db.add(Machine(name=name, kind=MachineKind.PRINTER, status=MachineStatus.FREE))
            print(f"добавлен: {name}")
        await db.commit()

        # Лишнее в базе не удаляем: на эти строки ссылается история работ.
        # Если машина уехала — выведи её в обслуживание из админки.
        if unknown:
            print(
                f"\nв базе есть принтеры не из PRINTER_NAMES: {', '.join(sorted(unknown))}\n"
                "они остались как есть — переименуй (rename_machine) или выведи "
                "в обслуживание из админки"
            )


async def list_machines() -> None:
    """Показать id, тип и имена: id нужен для rename_machine."""
    async with SessionLocal() as db:
        park = await machines_svc.list_machines(db)
        if not park:
            print("парк пуст — заведи машины в админке или: python -m app.cli seed_printers")
            return
        for machine in park:
            note = f" — {machine.note}" if machine.note else ""
            print(f"{machine.id}\t{machine.kind}\t{machine.name}\t{machine.status}{note}")


async def _find_machine(db, target: str) -> Machine | None:
    """Машина по id или по текущему имени."""
    machine = await db.get(Machine, int(target)) if target.isdigit() else None
    if machine is None:
        machine = await db.scalar(select(Machine).where(Machine.name == target))
    return machine


async def add_machine(name: str, kind: str) -> None:
    """Завести одну машину. Для случая, когда парк реально пополнился."""
    async with SessionLocal() as db:
        admin = await _cli_admin(db)
        try:
            machine = await machines_svc.create(db, admin, name, kind)
        except DomainError as error:
            print(error)
            return
        await db.commit()
        print(f"добавлен: {machine.name} ({machine.kind})")
        if machine.kind == MachineKind.PRINTER and machine.name not in settings.printers:
            print("допиши его в PRINTER_NAMES в .env, иначе seed_printers будет считать лишним")


async def remove_machine(target: str) -> None:
    """Удалить машину — только пустую, без единой работы и приглашения."""
    async with SessionLocal() as db:
        machine = await _find_machine(db, target)
        if machine is None:
            print(f"не нашёл машину {target!r} — посмотри: python -m app.cli machines")
            return

        admin = await _cli_admin(db)
        try:
            name = await machines_svc.remove(db, admin, machine.id)
        except DomainError as error:
            print(error)
            return
        await db.commit()
        print(f"удалён: {name}")


async def rename_machine(target: str, new_name: str) -> None:
    """Переименовать машину по id или текущему имени."""
    async with SessionLocal() as db:
        machine = await _find_machine(db, target)
        if machine is None:
            print(f"не нашёл машину {target!r} — посмотри: python -m app.cli machines")
            return

        admin = await _cli_admin(db)
        kind = machine.kind
        try:
            old_name = await machines_svc.rename(db, admin, machine.id, new_name)
        except DomainError as error:
            print(error)
            return
        await db.commit()
        print(f"{old_name} → {machine.name}")
        if kind == MachineKind.PRINTER and machine.name not in settings.printers:
            print(f"не забудь про PRINTER_NAMES в .env — там всё ещё {settings.printer_names!r}")


async def make_admin(tg_chat_id: int, name: str) -> None:
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.tg_chat_id == tg_chat_id))
        if user is None:
            pin = await pick_free_pin(db)
            user = User(
                tg_chat_id=tg_chat_id, name=name, pin_digest=pin_digest(pin), is_admin=True
            )
            db.add(user)
            await db.commit()
            print(f"создан админ {name} (tg {tg_chat_id}), PIN: {pin}")
            print("PIN больше нигде не хранится в открытом виде — запиши его сейчас")
        else:
            user.is_admin = True
            await db.commit()
            print(f"{user.name} (tg {tg_chat_id}) теперь админ")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed_printers", help="создать принтеры из PRINTER_NAMES")
    sub.add_parser("machines", help="показать машины с их id и типом")

    rename = sub.add_parser("rename_machine", help="переименовать машину")
    rename.add_argument("machine", help="id или текущее имя")
    rename.add_argument("name", help="новое имя")

    add = sub.add_parser("add_machine", help="завести одну машину")
    add.add_argument("name", help="имя новой машины")
    add.add_argument(
        "--kind",
        default=MachineKind.PRINTER.value,
        choices=[kind.value for kind in MachineKind],
        help="тип оборудования",
    )

    remove = sub.add_parser("remove_machine", help="удалить машину без истории")
    remove.add_argument("machine", help="id или имя")

    admin = sub.add_parser("make_admin", help="выдать права админа")
    admin.add_argument("tg_chat_id", type=int)
    admin.add_argument("--name", default="admin")

    args = parser.parse_args()

    async def run() -> None:
        try:
            if args.command == "seed_printers":
                await seed_printers()
            elif args.command == "machines":
                await list_machines()
            elif args.command == "rename_machine":
                await rename_machine(args.machine, args.name)
            elif args.command == "add_machine":
                await add_machine(args.name, args.kind)
            elif args.command == "remove_machine":
                await remove_machine(args.machine)
            elif args.command == "make_admin":
                await make_admin(args.tg_chat_id, args.name)
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
