from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

from app.bot import notify
from app.config import settings


@pytest.fixture
def sender(monkeypatch):
    sender = AsyncMock()
    monkeypatch.setattr(notify, "_sender", sender)
    monkeypatch.setattr(notify.asyncio, "sleep", AsyncMock())
    return sender


async def login(client):
    await client.post("/admin/login", data={"secret": settings.admin_secret})


async def test_all_users_and_refresh(client, make_user, sender):
    first = await make_user()
    second = await make_user()
    await login(client)
    response = await client.post("/admin/broadcasts", data={
        "audience": "all", "message": "Всем <привет> & удачи!",
    })
    assert response.status_code == 303
    assert "sent=2&failed=0" in response.headers["location"]
    assert [call.args for call in sender.await_args_list] == [
        (first.tg_chat_id, "Всем &lt;привет&gt; &amp; удачи!"),
        (second.tg_chat_id, "Всем &lt;привет&gt; &amp; удачи!"),
    ]
    for _ in range(2):
        result = await client.get(response.headers["location"])
        assert "Отправлено: 2. Не удалось отправить: 0." in result.text
    assert sender.await_count == 2


async def test_selected_users_are_deduplicated(client, make_user, sender):
    first = await make_user()
    await make_user()
    third = await make_user()
    await login(client)
    response = await client.post("/admin/broadcasts", data={
        "audience": "selected", "message": "Личное сообщение",
        "user_ids": [first.id, third.id, first.id],
    })
    assert response.status_code == 303
    assert [call.args[0] for call in sender.await_args_list] == [
        first.tg_chat_id, third.tg_chat_id,
    ]


@pytest.mark.parametrize("data", [
    {"audience": "all", "message": "  "},
    {"audience": "all", "message": "a" * 4097},
    {"audience": "all", "message": "😀" * 2049},
    {"audience": "selected", "message": "hello"},
    {"audience": "selected", "message": "hello", "user_ids": [99999]},
    {"audience": "invalid", "message": "hello"},
])
async def test_invalid_request_sends_nothing(client, make_user, sender, data):
    await make_user()
    await login(client)
    response = await client.post("/admin/broadcasts", data=data)
    assert response.status_code == 400
    assert 'role="alert"' in response.text
    sender.assert_not_awaited()


async def test_access_denied(client, sender):
    assert (await client.get("/admin/broadcasts")).status_code == 403
    response = await client.post("/admin/broadcasts", data={
        "audience": "all", "message": "hello",
    })
    assert response.status_code == 403
    sender.assert_not_awaited()


async def test_unconfigured_bot(client, monkeypatch):
    monkeypatch.setattr(notify, "_sender", None)
    await login(client)
    response = await client.post("/admin/broadcasts", data={
        "audience": "all", "message": "hello",
    })
    assert response.status_code == 400
    assert "Бот не подключён" in response.text


async def test_person_link_preselects_recipient(client, make_user, sender):
    person = await make_user()
    await login(client)
    response = await client.get(f"/admin/broadcasts?user_id={person.id}")
    assert f'value="{person.id}" checked' in response.text
    assert (await client.get("/admin/broadcasts?audience=all")).text.count(
        'value="all" checked'
    ) == 1


async def test_empty_audience(client, sender):
    await login(client)
    response = await client.post("/admin/broadcasts", data={
        "audience": "all", "message": "hello",
    })
    assert response.status_code == 400
    sender.assert_not_awaited()


async def test_delivery_failure_does_not_stop_broadcast(sender):
    sender.side_effect = [RuntimeError("blocked"), None]
    assert await notify.send_broadcast([101, 102], "hello") == (1, 1)
    assert sender.await_count == 2


async def test_flood_limit_retries_same_recipient(sender):
    sender.side_effect = [TelegramRetryAfter(
        method=SendMessage(chat_id=101, text="hello"), message="slow down", retry_after=2,
    ), None, None]
    assert await notify.send_broadcast([101, 102], "hello") == (2, 0)
    assert [call.args[0] for call in sender.await_args_list] == [101, 101, 102]
    notify.asyncio.sleep.assert_any_await(2)
