"""Ошибки бизнес-правил.

Текст сообщения показывается человеку на киоске или в боте, поэтому пишется
по-русски и объясняет, что делать, а не что сломалось внутри.
"""


class DomainError(Exception):
    """Базовая ошибка правил системы."""


class InvalidDuration(DomainError):
    """Длительность работы вне допустимых границ."""


class MachineNotAvailable(DomainError):
    """Машина занята, сломана или иначе недоступна."""


class MachineReserved(DomainError):
    """Правило 7: машина зарезервирована за первым в очереди своего типа."""


class MachineReleaseForbidden(DomainError):
    """Активную работу может снять только её владелец или админ."""


class MachineNameTaken(DomainError):
    """Имя уже носит другая машина парка."""


class MachineNameInvalid(DomainError):
    """Имя пустое или длиннее, чем влезает в строку таблицы."""


class MachineKindUnknown(DomainError):
    """Тип оборудования не из `MachineKind`."""


class MachineHasHistory(DomainError):
    """Машину с работами и приглашениями в журнале удалять нельзя."""


class UserBusy(DomainError):
    """Правило 2: у человека уже есть активная сессия."""


class AlreadyInQueue(DomainError):
    """Правило 2: человек уже стоит в очереди."""


class NotInQueue(DomainError):
    """Человек не в очереди."""


class OfferNotActive(DomainError):
    """Предложение уже неактуально или окно ещё не истекло."""


class NotAdmin(DomainError):
    """Действие доступно только админу."""


class AuthFailed(DomainError):
    """Неверный PIN, протухшая ссылка или чужая подпись."""


class TooManyAttempts(DomainError):
    """Слишком часто — включилась пауза."""


class PinTaken(DomainError):
    """PIN уже занят другим человеком."""


class LoginInvalid(DomainError):
    """Строка не похожа на корпоративный логин."""


class LoginTaken(DomainError):
    """Логин уже носит другой человек."""
