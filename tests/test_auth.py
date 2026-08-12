import pytest

from app.config import settings
from app.services import auth
from app.services.errors import AuthFailed, PinTaken, TooManyAttempts
from app.services.security import pin_digest


@pytest.fixture(autouse=True)
def clean_limiters():
    """Ограничители держат состояние в памяти процесса — сбрасываем между тестами."""
    auth.pin_limiter._failures.clear()
    auth.pin_limiter._locked_until.clear()
    yield


class TestTokens:
    def test_device_token_roundtrip(self):
        assert auth.is_kiosk_device(auth.issue_device_token()) is True

    def test_device_token_rejects_garbage(self):
        assert auth.is_kiosk_device("подделка") is False
        assert auth.is_kiosk_device(None) is False

    def test_device_token_dies_on_secret_rotation(self, monkeypatch):
        """Единственный способ отозвать киоски — ротация KIOSK_SECRET."""
        token = auth.issue_device_token()
        monkeypatch.setattr(settings, "kiosk_secret", "новый-секрет")

        assert auth.is_kiosk_device(token) is False

    def test_admin_token_is_not_a_device_token(self):
        """Разные соли: подпись админки не превращает ноутбук в киоск."""
        assert auth.is_kiosk_device(auth.issue_admin_session()) is False

    def test_admin_session_roundtrip(self):
        assert auth.is_admin_session(auth.issue_admin_session()) is True
        assert auth.is_admin_session("нет") is False


class TestLimiters:
    def test_lockout_after_five_failures(self):
        limiter = auth.AttemptLimiter(max_attempts=5, lockout_seconds=60)
        for _ in range(5):
            limiter.ensure_allowed("k", now=0)
            limiter.register_failure("k", now=0)

        with pytest.raises(TooManyAttempts, match="подождите"):
            limiter.ensure_allowed("k", now=10)

    def test_lockout_releases_after_timeout(self):
        limiter = auth.AttemptLimiter(max_attempts=2, lockout_seconds=60)
        for _ in range(2):
            limiter.register_failure("k", now=0)

        limiter.ensure_allowed("k", now=61)

    def test_success_resets_counter(self):
        limiter = auth.AttemptLimiter(max_attempts=2, lockout_seconds=60)
        limiter.register_failure("k", now=0)
        limiter.reset("k")
        limiter.register_failure("k", now=0)

        limiter.ensure_allowed("k", now=0)


class TestPin:
    async def test_find_user_by_pin(self, db, make_user):
        user = await make_user(pin="4242")

        assert (await auth.user_by_pin(db, "4242")).id == user.id

    async def test_unknown_pin_rejected(self, db, make_user):
        await make_user(pin="4242")

        with pytest.raises(AuthFailed, match="Неверный PIN"):
            await auth.user_by_pin(db, "1111")

    @pytest.mark.parametrize("pin", ["", "12", "12345", "abcd", "12a4"])
    async def test_bad_format_rejected(self, db, pin):
        with pytest.raises(AuthFailed, match="четыре цифры"):
            await auth.user_by_pin(db, pin)

    async def test_pin_is_unique_across_users(self, db, make_user):
        await make_user(pin="4242")
        other = await make_user(pin="7777")

        with pytest.raises(PinTaken):
            await auth.set_pin(db, other, "4242")

    async def test_assign_pin_gives_working_code(self, db, make_user):
        user = await make_user()

        pin = await auth.assign_pin(db, user)

        assert (await auth.user_by_pin(db, pin)).id == user.id

    async def test_pin_is_not_stored_in_clear(self, db, make_user):
        user = await make_user(pin="4242")

        assert user.pin_digest != "4242"
        assert user.pin_digest == pin_digest("4242")


class TestKioskRoutes:
    async def test_enroll_requires_secret(self, client):
        response = await client.get("/kiosk/enroll?secret=неверно")

        assert response.status_code == 403
        assert auth.DEVICE_COOKIE not in response.cookies

    async def test_enroll_sets_device_cookie(self, client):
        response = await client.get(f"/kiosk/enroll?secret={settings.kiosk_enroll_secret}")

        assert response.status_code == 303
        assert auth.is_kiosk_device(client.cookies.get(auth.DEVICE_COOKIE))

    async def test_pin_is_refused_on_foreign_device(self, client, printers, make_user):
        """Правило 11: с ноутбука PIN ввести негде."""
        await make_user(pin="4242")

        response = await client.post(
            f"/occupy/{printers[0].id}", data={"pin": "4242", "minutes": "60"}
        )

        assert response.status_code == 403

    async def test_action_leaves_no_session_behind(self, client, printers, make_user):
        """PIN подписывает одно действие и не переносится на следующее."""
        await make_user(pin="4242")
        await client.get(f"/kiosk/enroll?secret={settings.kiosk_enroll_secret}")

        await client.post(f"/occupy/{printers[0].id}", data={"pin": "4242", "minutes": "60"})

        assert set(client.cookies) == {auth.DEVICE_COOKIE}

    async def test_wrong_pin_is_rejected_then_locked_out(self, client, printers, make_user):
        await make_user(pin="4242")
        await client.get(f"/kiosk/enroll?secret={settings.kiosk_enroll_secret}")
        machine_id = printers[0].id

        for _ in range(5):
            failed = await client.post(
                f"/occupy/{machine_id}", data={"pin": "0000", "minutes": "60"}
            )
            assert failed.status_code == 401

        locked = await client.post(f"/occupy/{machine_id}", data={"pin": "4242", "minutes": "60"})
        assert locked.status_code == 429


class TestAdminAndService:
    async def test_logout_clears_admin_but_keeps_kiosk(self, client):
        await client.get(f"/kiosk/enroll?secret={settings.kiosk_enroll_secret}")
        await client.post("/admin/login", data={"secret": settings.admin_secret})

        await client.get("/logout")

        assert not client.cookies.get(auth.ADMIN_COOKIE)
        assert auth.is_kiosk_device(client.cookies.get(auth.DEVICE_COOKIE))

    async def test_admin_login_requires_secret(self, client):
        response = await client.post("/admin/login", data={"secret": "нет"})

        assert response.status_code == 403

    async def test_admin_login_sets_cookie(self, client):
        response = await client.post("/admin/login", data={"secret": settings.admin_secret})

        assert response.status_code == 303
        assert auth.is_admin_session(client.cookies.get(auth.ADMIN_COOKIE))

    async def test_robots_closes_the_site(self, client):
        response = await client.get("/robots.txt")

        assert "Disallow: /" in response.text
