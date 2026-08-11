"""Ошибки бизнес-правил.

Текст сообщения показывается человеку на киоске или в боте, поэтому пишется
по-русски и объясняет, что делать, а не что сломалось внутри.
"""


class DomainError(Exception):
    """Базовая ошибка правил системы."""


class InvalidDuration(DomainError):
    """Длительность печати вне допустимых границ."""


class PrinterNotAvailable(DomainError):
    """Принтер занят, сломан или иначе недоступен."""


class PrinterReserved(DomainError):
    """Правило 7: принтер зарезервирован за первым в очереди."""


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
