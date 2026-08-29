"""Ошибки бизнес-правил.

Текст сообщения показывается человеку на киоске или в боте, поэтому пишется
по-русски и объясняет, что делать, а не что сломалось внутри.
"""


class DomainError(Exception):
    """Базовая ошибка правил системы."""


class InvalidDuration(DomainError):
    """Длительность работы вне допустимых границ."""


class RoomNotFound(DomainError):
    """Помещения с таким номером нет."""


class RoomNameTaken(DomainError):
    """Имя уже носит другое помещение."""


class RoomNameInvalid(DomainError):
    """Имя помещения пустое или длиннее, чем влезает в строку таблицы."""


class RoomKindUnknown(DomainError):
    """Тип помещения не из `RoomKind`."""


class RoomNotEmpty(DomainError):
    """В помещении ещё стоит оборудование — удалять его вместе с комнатой нельзя."""


class MachineKindNotInRoom(DomainError):
    """Такую единицу в помещении этого типа не заводят: принтер — не переговорная."""


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


class MachineBooked(DomainError):
    """Правило 12: машина забронирована — сейчас или раньше, чем закончится работа."""


class ReservationNotFound(DomainError):
    """Брони нет или она уже закрыта."""


class ReservationForbidden(DomainError):
    """Чужую бронь отменяет только админ."""


class ReservationOverlap(DomainError):
    """Окно пересекается с другой бронью или с идущей работой."""


class AlreadyBooked(DomainError):
    """В обычном режиме у человека уже есть незакрытая задача."""


class InvalidReservationTime(DomainError):
    """Начало не по сетке, в прошлом, дальше горизонта или вне рабочих часов."""


class WorkHoursInvalid(DomainError):
    """Часы работы из формы админки не разобрались или закрытие раньше открытия."""


class UserBusy(DomainError):
    """Правило 2: у человека уже есть активная сессия."""


class UserLimitReached(DomainError):
    """Расширенная квота выбранного типа оборудования уже исчерпана."""


class AlreadyInQueue(DomainError):
    """Правило 2: человек уже стоит в очереди."""


class NotInQueue(DomainError):
    """Человек не в очереди."""


class OfferNotActive(DomainError):
    """Предложение уже неактуально или окно ещё не истекло."""


class ChatIdInvalid(DomainError):
    """Telegram chat id — целое положительное число."""


class ChatIdTaken(DomainError):
    """Этот Telegram уже привязан к другому человеку."""


class LastAdmin(DomainError):
    """Последнего админа не удаляем: от его имени пишется каждое действие
    панели, и без него админка закрылась бы сама на себя."""


class LastSuperadmin(DomainError):
    """Последний суперадмин должен оставаться в системе."""


class SuperadminRequired(DomainError):
    """Назначать администраторов может только суперадмин."""


class UserNotFound(DomainError):
    """Человека с таким номером нет."""


class NotAdmin(DomainError):
    """Действие доступно только админу."""


class AuthFailed(DomainError):
    """Неверный PIN, протухшая ссылка или чужая подпись."""


class TooManyAttempts(DomainError):
    """Слишком часто — включилась пауза."""


class BadInitData(DomainError):
    """Mini App открыт не из Telegram или его подпись не сходится."""


class AppSessionRequired(DomainError):
    """Сессия Mini App истекла — приложение надо открыть заново из бота."""


class PinTaken(DomainError):
    """PIN уже занят другим человеком."""


class LoginInvalid(DomainError):
    """Строка не похожа на корпоративный логин."""


class LoginTaken(DomainError):
    """Логин уже носит другой человек."""


class FeedbackInvalid(DomainError):
    """Имя или текст обращения пустые либо не помещаются в форму."""
