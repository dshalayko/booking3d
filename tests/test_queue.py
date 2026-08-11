from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.enums import QueueStatus
from app.models import QueueEntry
from app.services import printers as printers_svc
from app.services import queue as svc
from app.services.errors import AlreadyInQueue, NotInQueue, OfferNotActive, UserBusy

NOON = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


async def occupy_both(db, printers, make_user):
    """Оба принтера заняты — обычное состояние парка из двух машин."""
    first = await make_user()
    second = await make_user()
    await printers_svc.occupy(db, first, printers[0].id, 60, now=NOON)
    await printers_svc.occupy(db, second, printers[1].id, 60, now=NOON)
    return first, second


class TestJoinLeave:
    async def test_join_gives_position(self, db, printers, make_user):
        await occupy_both(db, printers, make_user)
        first = await make_user()
        second = await make_user()

        first_join = await svc.join(db, first.id, now=NOON + timedelta(minutes=1))
        second_join = await svc.join(db, second.id, now=NOON + timedelta(minutes=2))

        assert first_join.position == 1
        assert second_join.position == 2

    async def test_join_twice_is_rejected(self, db, printers, make_user):
        await occupy_both(db, printers, make_user)
        user = await make_user()
        await svc.join(db, user.id, now=NOON)

        with pytest.raises(AlreadyInQueue):
            await svc.join(db, user.id, now=NOON)

    async def test_user_with_active_print_cannot_queue(self, db, printers, make_user):
        """Иначе один человек держит принтер и место на второй."""
        user = await make_user()
        await printers_svc.occupy(db, user, printers[0].id, 60, now=NOON)

        with pytest.raises(UserBusy):
            await svc.join(db, user.id, now=NOON)

    async def test_leave_removes_from_queue(self, db, printers, make_user):
        await occupy_both(db, printers, make_user)
        user = await make_user()
        await svc.join(db, user.id, now=NOON)

        await svc.leave(db, user.id, now=NOON)

        assert await svc.position_of(db, user.id) is None

    async def test_leave_without_queue_is_rejected(self, db, printers, make_user):
        user = await make_user()

        with pytest.raises(NotInQueue):
            await svc.leave(db, user.id, now=NOON)

    async def test_leaving_offered_slot_passes_it_on(self, db, printers, make_user):
        owner, _ = await occupy_both(db, printers, make_user)
        first = await make_user()
        second = await make_user()
        await svc.join(db, first.id, now=NOON + timedelta(minutes=1))
        await svc.join(db, second.id, now=NOON + timedelta(minutes=2))
        await printers_svc.release(db, owner, printers[0].id, now=NOON + timedelta(minutes=30))

        result = await svc.leave(db, first.id, now=NOON + timedelta(minutes=31))

        assert [offer.user_id for offer in result.offers] == [second.id]

    async def test_queue_order_is_fifo(self, db, printers, make_user):
        await occupy_both(db, printers, make_user)
        people = [await make_user() for _ in range(3)]
        for index, person in enumerate(people):
            await svc.join(db, person.id, now=NOON + timedelta(minutes=index))

        entries = await svc.active_entries(db)

        assert [entry.user_id for entry in entries] == [person.id for person in people]


class TestOffers:
    async def test_offer_goes_only_to_the_first(self, db, printers, make_user):
        """Правило 4: рассылка всем превратила бы очередь в гонку."""
        owner, _ = await occupy_both(db, printers, make_user)
        first = await make_user()
        second = await make_user()
        await svc.join(db, first.id, now=NOON + timedelta(minutes=1))
        await svc.join(db, second.id, now=NOON + timedelta(minutes=2))

        result = await printers_svc.release(db, owner, printers[0].id, now=NOON)

        assert [offer.user_id for offer in result.offers] == [first.id]
        second_entry = await db.scalar(select(QueueEntry).where(QueueEntry.user_id == second.id))
        assert second_entry.status == QueueStatus.WAITING

    async def test_queue_is_shared_between_printers(self, db, printers, make_user):
        """Правило 3: две отдельные очереди дали бы «свободен #2, а первый ждёт #1»."""
        _, owner_of_second = await occupy_both(db, printers, make_user)
        waiting = await make_user()
        await svc.join(db, waiting.id, now=NOON)

        result = await printers_svc.release(db, owner_of_second, printers[1].id, now=NOON)

        assert len(result.offers) == 1
        assert result.offers[0].printer_id == printers[1].id
        assert result.offers[0].user_id == waiting.id

    async def test_two_free_printers_give_two_offers(self, db, printers, make_user):
        owner_one, owner_two = await occupy_both(db, printers, make_user)
        first = await make_user()
        second = await make_user()
        third = await make_user()
        await svc.join(db, first.id, now=NOON + timedelta(minutes=1))
        await svc.join(db, second.id, now=NOON + timedelta(minutes=2))
        await svc.join(db, third.id, now=NOON + timedelta(minutes=3))

        await printers_svc.release(db, owner_one, printers[0].id, now=NOON)
        await printers_svc.release(db, owner_two, printers[1].id, now=NOON)

        entries = {entry.user_id: entry.status for entry in await svc.active_entries(db)}
        assert entries[first.id] == QueueStatus.OFFERED
        assert entries[second.id] == QueueStatus.OFFERED
        assert entries[third.id] == QueueStatus.WAITING

    async def test_join_when_printer_already_free_offers_immediately(
        self, db, printers, make_user
    ):
        user = await make_user()

        result = await svc.join(db, user.id, now=NOON)

        assert [offer.user_id for offer in result.offers] == [user.id]

    async def test_window_is_thirty_minutes_in_daytime(self, db, printers, make_user):
        owner, _ = await occupy_both(db, printers, make_user)
        waiting = await make_user()
        await svc.join(db, waiting.id, now=NOON)

        result = await printers_svc.release(db, owner, printers[0].id, now=NOON)

        assert result.offers[0].expires_at == NOON + timedelta(minutes=30)

    async def test_night_release_keeps_offer_until_morning(self, db, printers, make_user):
        """Правило 6: иначе предложение сгорит в 03:40 и очередь опустеет впустую."""
        owner, _ = await occupy_both(db, printers, make_user)
        waiting = await make_user()
        await svc.join(db, waiting.id, now=NOON)
        night = datetime(2026, 8, 11, 0, 0, tzinfo=UTC)  # 03:00 по Никосии

        result = await printers_svc.release(db, owner, printers[0].id, now=night)

        # 08:30 по Никосии = 05:30 UTC
        assert result.offers[0].expires_at == datetime(2026, 8, 11, 5, 30, tzinfo=UTC)


class TestExpiry:
    async def test_expired_offer_passes_to_next(self, db, printers, make_user):
        """Правило 5: без окна первый в очереди блокировал бы принтер бесконечно."""
        owner, _ = await occupy_both(db, printers, make_user)
        first = await make_user()
        second = await make_user()
        await svc.join(db, first.id, now=NOON + timedelta(minutes=1))
        await svc.join(db, second.id, now=NOON + timedelta(minutes=2))
        release = await printers_svc.release(db, owner, printers[0].id, now=NOON)
        offer = release.offers[0]

        result = await svc.expire_offer(db, offer.entry_id, now=NOON + timedelta(minutes=31))

        assert result.user_id == first.id
        assert [passed.user_id for passed in result.offers] == [second.id]
        assert (await db.get(QueueEntry, offer.entry_id)).status == QueueStatus.EXPIRED

    async def test_expiring_too_early_is_rejected(self, db, printers, make_user):
        owner, _ = await occupy_both(db, printers, make_user)
        waiting = await make_user()
        await svc.join(db, waiting.id, now=NOON)
        release = await printers_svc.release(db, owner, printers[0].id, now=NOON)

        with pytest.raises(OfferNotActive, match="не истекло"):
            await svc.expire_offer(db, release.offers[0].entry_id, now=NOON + timedelta(minutes=5))

    async def test_expiring_twice_is_rejected(self, db, printers, make_user):
        owner, _ = await occupy_both(db, printers, make_user)
        waiting = await make_user()
        await svc.join(db, waiting.id, now=NOON)
        release = await printers_svc.release(db, owner, printers[0].id, now=NOON)
        later = NOON + timedelta(minutes=31)
        await svc.expire_offer(db, release.offers[0].entry_id, now=later)

        with pytest.raises(OfferNotActive):
            await svc.expire_offer(db, release.offers[0].entry_id, now=later)

    async def test_expired_user_can_join_again(self, db, printers, make_user):
        owner, _ = await occupy_both(db, printers, make_user)
        waiting = await make_user()
        await svc.join(db, waiting.id, now=NOON)
        release = await printers_svc.release(db, owner, printers[0].id, now=NOON)
        await svc.expire_offer(db, release.offers[0].entry_id, now=NOON + timedelta(minutes=31))

        again = await svc.join(db, waiting.id, now=NOON + timedelta(minutes=40))

        assert again.position == 1
