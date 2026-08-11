"""Служебные команды.

    python -m app.cli seed_printers
    python -m app.cli printers
    python -m app.cli rename_printer <id-или-имя> <новое имя>
    python -m app.cli add_printer <имя>
    python -m app.cli remove_printer <id-или-имя>
    python -m app.cli make_admin <tg_chat_id> [--name Имя]

Про имена принтеров. Парк объявлен в `PRINTER_NAMES` (.env), но `seed_printers`
только создаёт недостающее — переименовать уже созданный принтер правкой .env
нельзя, и это не недоделка. Имя лежит в строке таблицы `printers`, на которую
ссылаются сессии и очередь; менять его по совпадению позиции в списке значило бы
переименовать машину, угадав, какая из них какая. Поэтому переименование —
отдельная явная команда по id.
"""

import argparse
import asyncio

from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal, engine
from app.enums import PrinterStatus
from app.models import Printer, PrintSession, QueueEntry, User
from app.services.auth import pick_free_pin
from app.services.security import pin_digest


async def seed_printers() -> None:
    """Создать принтеры, объявленные в PRINTER_NAMES. Существующие не трогает."""
    async with SessionLocal() as db:
        existing = set((await db.scalars(select(Printer.name))).all())
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
                "  python -m app.cli printers\n"
                '  python -m app.cli rename_printer <id> "<новое имя>"\n\n'
                "если принтеры правда новые — заведи их по одному "
                "(add_printer), а уехавшие выведи в обслуживание из админки."
            )
            return

        for name in settings.printers:
            if name in existing:
                print(f"уже есть: {name}")
                continue
            db.add(Printer(name=name, status=PrinterStatus.FREE))
            print(f"добавлен: {name}")
        await db.commit()

        # Лишнее в базе не удаляем: на эти строки ссылается история печатей.
        # Если принтер уехал — выведи его в обслуживание из админки.
        if unknown:
            print(
                f"\nв базе есть принтеры не из PRINTER_NAMES: {', '.join(sorted(unknown))}\n"
                "они остались как есть — переименуй (rename_printer) или выведи "
                "в обслуживание из админки"
            )


async def list_printers() -> None:
    """Показать id и имена: id нужен для rename_printer."""
    async with SessionLocal() as db:
        printers = (await db.scalars(select(Printer).order_by(Printer.id))).all()
        if not printers:
            print("принтеров нет — создай: python -m app.cli seed_printers")
            return
        for printer in printers:
            note = f" — {printer.note}" if printer.note else ""
            print(f"{printer.id}\t{printer.name}\t{printer.status}{note}")


async def _find_printer(db, target: str) -> Printer | None:
    """Принтер по id или по текущему имени."""
    printer = await db.get(Printer, int(target)) if target.isdigit() else None
    if printer is None:
        printer = await db.scalar(select(Printer).where(Printer.name == target))
    return printer


async def add_printer(name: str) -> None:
    """Завести один принтер. Для случая, когда парк реально пополнился."""
    name = name.strip()
    if not name:
        print("имя пустое")
        return

    async with SessionLocal() as db:
        if await db.scalar(select(Printer.id).where(Printer.name == name)) is not None:
            print(f"принтер {name!r} уже есть")
            return
        db.add(Printer(name=name, status=PrinterStatus.FREE))
        await db.commit()
        print(f"добавлен: {name}")
        if name not in settings.printers:
            print("допиши его в PRINTER_NAMES в .env, иначе seed_printers будет считать лишним")


async def remove_printer(target: str) -> None:
    """Удалить принтер — только пустой, без единой печати и приглашения.

    Принтер с историей не удаляем: на него ссылаются `sessions` и `queue`, и
    удаление либо упало бы на внешнем ключе, либо оторвало журнал от машины.
    Уехавший принтер с историей выводится в обслуживание из админки — он
    останется на доске, но занять его будет нельзя.
    """
    async with SessionLocal() as db:
        printer = await _find_printer(db, target)
        if printer is None:
            print(f"не нашёл принтер {target!r} — посмотри: python -m app.cli printers")
            return

        sessions = await db.scalar(
            select(func.count()).select_from(PrintSession).where(
                PrintSession.printer_id == printer.id
            )
        )
        offers = await db.scalar(
            select(func.count()).select_from(QueueEntry).where(
                QueueEntry.offered_printer_id == printer.id
            )
        )
        if sessions or offers:
            print(
                f"не удаляю {printer.name}: за ним {sessions} печат(и) и {offers} "
                "приглашени(й) в журнале.\n"
                "если машина уехала — выведи её в обслуживание из админки, "
                "тогда история останется целой."
            )
            return

        name = printer.name
        await db.delete(printer)
        await db.commit()
        print(f"удалён: {name}")


async def rename_printer(target: str, new_name: str) -> None:
    """Переименовать принтер по id или текущему имени.

    История остаётся: меняется имя той же строки, а не создаётся новая. Поэтому
    в журнале старые печати покажутся уже под новым именем — это осознанно,
    физическая машина в том же углу, и два имени в журнале путали бы сильнее.
    """
    new_name = new_name.strip()
    if not new_name:
        print("новое имя пустое")
        return

    async with SessionLocal() as db:
        printer = await _find_printer(db, target)
        if printer is None:
            print(f"не нашёл принтер {target!r} — посмотри: python -m app.cli printers")
            return

        taken = await db.scalar(select(Printer.id).where(Printer.name == new_name))
        if taken is not None and taken != printer.id:
            print(f"имя {new_name!r} уже занято принтером {taken}")
            return

        old_name = printer.name
        printer.name = new_name
        await db.commit()
        print(f"{old_name} → {new_name}")
        if new_name not in settings.printers:
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
    sub.add_parser("printers", help="показать принтеры с их id")

    rename = sub.add_parser("rename_printer", help="переименовать принтер")
    rename.add_argument("printer", help="id или текущее имя")
    rename.add_argument("name", help="новое имя")

    add = sub.add_parser("add_printer", help="завести один принтер")
    add.add_argument("name", help="имя нового принтера")

    remove = sub.add_parser("remove_printer", help="удалить принтер без истории")
    remove.add_argument("printer", help="id или имя")

    admin = sub.add_parser("make_admin", help="выдать права админа")
    admin.add_argument("tg_chat_id", type=int)
    admin.add_argument("--name", default="admin")

    args = parser.parse_args()

    async def run() -> None:
        try:
            if args.command == "seed_printers":
                await seed_printers()
            elif args.command == "printers":
                await list_printers()
            elif args.command == "rename_printer":
                await rename_printer(args.printer, args.name)
            elif args.command == "add_printer":
                await add_printer(args.name)
            elif args.command == "remove_printer":
                await remove_printer(args.printer)
            elif args.command == "make_admin":
                await make_admin(args.tg_chat_id, args.name)
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
