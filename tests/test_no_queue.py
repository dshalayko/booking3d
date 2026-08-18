from datetime import UTC, datetime

from app.enums import MachineKind
from app.services import machines as machines_svc
from app.services import queue as legacy_queue_svc

NOON = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


async def test_queue_routes_are_removed(client, room):
    paths = (
        f"/queue/join/{room.id}/{MachineKind.PRINTER}",
        f"/queue/leave/{room.id}",
        f"/app/queue/join/{room.id}/{MachineKind.PRINTER}",
        f"/app/queue/leave/{room.id}",
    )

    for path in paths:
        assert (await client.get(path)).status_code == 404
        assert (await client.post(path)).status_code == 404


async def test_board_only_offers_schedule_when_all_machines_are_busy(
    client, room, db, printers, make_user
):
    for printer in printers:
        await machines_svc.occupy(db, await make_user(), printer.id, 60, now=NOON)
    await db.commit()

    response = await client.get(f"/room/{room.id}")

    assert response.status_code == 200
    assert "Встать в очередь" not in response.text
    assert f"/schedule/{room.id}/{MachineKind.PRINTER}" in response.text


async def test_legacy_queue_entry_does_not_reserve_a_free_machine(
    db, room, printers, make_user
):
    owner = await make_user()
    waiting = await make_user()
    newcomer = await make_user()
    await machines_svc.occupy(db, owner, printers[0].id, 60, now=NOON)
    await machines_svc.occupy(db, await make_user(), printers[1].id, 60, now=NOON)
    await legacy_queue_svc.join(
        db, waiting.id, room.id, MachineKind.PRINTER, now=NOON
    )
    await machines_svc.release(db, owner, printers[0].id, now=NOON)

    await machines_svc.occupy(db, newcomer, printers[0].id, 60, now=NOON)
