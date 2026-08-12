from datetime import time
from functools import cached_property
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tz: str = "Europe/Nicosia"
    public_base_url: str = "http://localhost:8000"

    # Язык интерфейса: см. app/texts/__init__.py. Имя не `lang` — иначе
    # pydantic-settings подхватит системную переменную локали `LANG`.
    ui_lang: str = "ru"

    database_url: str

    # Принтеры для первого запуска, через запятую. Без дефолта намеренно: имя
    # принтера — это то, что написано на стене конкретного коворкинга, и второй
    # его копии в коде быть не должно. Нет строки в .env — приложение не
    # поднимется, и это лучше, чем парк «P2S #1, P2S #2», взявшийся неизвестно
    # откуда.
    #
    # Дальше состав парка живёт в таблице `machines` и правится из админки:
    # `cli seed_printers` только досоздаёт недостающие принтеры по имени.
    # Гравировщиков здесь нет и быть не может — их заводят руками.
    # Разбор — в свойстве `printers` ниже.
    printer_names: str

    tg_bot_token: str = ""

    # Снимает привязку ввода PIN к зарегистрированному планшету: занимать
    # принтеры и вставать в очередь можно с любого устройства. Нужно, чтобы
    # прогнать цикл до того, как iPad повешен на стену.
    #
    # Дефолт `False` намеренно: с открытым доступом четыре цифры PIN
    # перебираются из интернета, и единственной защитой остаётся пауза после
    # пяти неудач — на один адрес, а не на весь мир. Держать включённым дольше
    # тестов нельзя. См. services/auth.py, правило 11 в PLAN.md.
    kiosk_open_access: bool = False

    # Снимает проверку подписи Telegram у Mini App: `/app` открывается в обычном
    # браузере, а войти можно любым зарегистрированным человеком, выбрав его из
    # списка. Нужно, чтобы прогнать брони и расписание до того, как заведён бот
    # и получен сертификат — Telegram не открывает мини-приложения по http.
    #
    # Дефолт `False` намеренно, и цена ошибки здесь выше, чем у
    # `KIOSK_OPEN_ACCESS`: там для действия всё ещё нужен PIN, а здесь не нужно
    # ничего — любой, кто открыл адрес, действует от чужого имени. На публичном
    # сервере флаг не включать никогда. См. api/miniapp.py, правило 11 в PLAN.md.
    miniapp_open_access: bool = False

    session_secret: str = ""
    kiosk_secret: str = ""
    kiosk_enroll_secret: str = ""
    admin_secret: str = ""
    pin_pepper: str = ""

    offer_window_minutes: int = 30
    night_start: time = time(23, 0)
    night_end: time = time(8, 0)
    unclaimed_ping_minutes: int = 60
    warn_before_minutes: int = 15
    reconcile_seconds: int = 60

    # Ночная работа считается до утра, а не фиксированные 12 часов: печать,
    # поставленная в 21:00, заканчивается к открытию, а не в 09:00 ровно через
    # полсуток. Отсюда берётся и кнопка «до утра», и потолок длительности брони.
    night_until: time = time(9, 0)

    # --- брони на будущее ---
    # Горизонт: дальше двух недель бронь — это не план, а место, которое человек
    # займёт и забудет.
    reservation_horizon_days: int = 14
    # Шаг сетки календаря. Начало брони кратно ему же: без выравнивания
    # расписание превращается в лоскуты по 10 минут, которые никому не годятся.
    reservation_slot_minutes: int = 60
    reservation_min_minutes: int = 60
    # Сколько ждём человека с начала его окна. Тикает только пока машина
    # свободна: чужая незабранная деталь не должна съедать чужую бронь.
    reservation_grace_minutes: int = 30
    # За сколько напомнить о начале брони — и владельцу текущей работы о том,
    # что после него придут.
    reservation_remind_minutes: int = 60

    @cached_property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @cached_property
    def printers(self) -> tuple[str, ...]:
        """Имена принтеров из `PRINTER_NAMES`.

        Разделитель — запятая, поэтому запятой в имени принтера быть не может.
        Имя видно на стене и в каждом сообщении бота, так что «P2S #1» лучше,
        чем «Bambu Lab P2S Combo (левый, у окна)».
        """
        names = tuple(name.strip() for name in self.printer_names.split(",") if name.strip())
        if not names:
            raise RuntimeError("PRINTER_NAMES пуст — впиши хотя бы один принтер в .env")
        if len(set(names)) != len(names):
            raise RuntimeError(f"PRINTER_NAMES: имена повторяются — {self.printer_names!r}")
        return names


def load(**overrides) -> Settings:
    """Настройки с внятной ошибкой на незаполненном .env.

    Своё сообщение вместо pydantic-овского: то печатает имя поля в нижнем
    регистре и ссылку на документацию, а читает его тот, у кого контейнер не
    поднялся и кому нужно знать, какую строку дописать.

    `overrides` нужны тесту, чтобы проверить это сообщение, не подкладывая
    временный .env.
    """
    try:
        return Settings(**overrides)
    except ValidationError as error:
        missing = [
            str(item["loc"][0]).upper()
            for item in error.errors()
            if item["type"] == "missing" and item["loc"]
        ]
        if not missing:
            raise
        raise RuntimeError(
            f"не задано в .env: {', '.join(missing)}. "
            "Загляни в .env.example — там эти строки с пояснениями."
        ) from error


settings = load()
