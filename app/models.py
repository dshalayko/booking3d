from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.enums import PrinterStatus


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # HMAC от PIN: уникальный, потому что на киоске человек вводит только его,
    # и по нему нужно однозначно опознать, кто это. См. services/security.py.
    pin_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<User {self.id} {self.name}>"


class Printer(Base):
    __tablename__ = "printers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{PrinterStatus.FREE}'")
    )
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Printer {self.name} {self.status}>"


class PrintSession(Base):
    """Одно занятие принтера человеком. Таблица называется sessions."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    eta_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    freed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Отметки об отправленных напоминаниях: планировщик сверяет состояние с
    # часами каждую минуту, и это защита от повторной отправки.
    warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unclaimed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Правило 1: одна активная сессия на принтер.
        Index(
            "one_active_session_per_printer",
            "printer_id",
            unique=True,
            postgresql_where=text("status IN ('printing', 'done_wait')"),
        ),
        # Правило 2: одна активная сессия на человека.
        Index(
            "one_active_session_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('printing', 'done_wait')"),
        ),
    )

    def __repr__(self) -> str:
        return f"<PrintSession {self.id} printer={self.printer_id} {self.status}>"


class QueueEntry(Base):
    __tablename__ = "queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    offered_printer_id: Mapped[int | None] = mapped_column(ForeignKey("printers.id"))
    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offer_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Когда запись закрылась: занял, окно истекло или вышел сам.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Правило 2: одно место в очереди на человека.
        Index(
            "one_queue_entry_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('waiting', 'offered')"),
        ),
        # Порядок очереди: FIFO по created_at среди активных.
        Index("queue_active_order", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<QueueEntry {self.id} user={self.user_id} {self.status}>"
