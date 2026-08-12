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

from app.enums import MachineKind, MachineStatus


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


class Machine(Base):
    """Единица парка: 3D-принтер или гравировщик.

    Одна таблица на все типы, а не таблица на тип: занятие, освобождение,
    поломка, очередь и журнал устроены одинаково, и вторая копия этой логики
    разошлась бы с первой на первой же правке. Различает машины `kind`.
    """

    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Имя уникально по всему парку, а не внутри типа: человек видит его на
    # экране и в сообщении бота без пометки типа, и два «№1» рядом
    # означали бы, что подошедший не знает, к какой машине идти.
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{MachineKind.PRINTER}'")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{MachineStatus.FREE}'")
    )
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Machine {self.name} {self.kind} {self.status}>"


class MachineSession(Base):
    """Одно занятие машины человеком. Таблица называется sessions."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
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
        # Правило 1: одна активная сессия на машину.
        Index(
            "one_active_session_per_machine",
            "machine_id",
            unique=True,
            postgresql_where=text("status IN ('printing', 'done_wait')"),
        ),
        # Правило 2: одна активная сессия на человека — на весь парк, а не на
        # тип. Занял принтер — освободи, прежде чем брать гравировщик.
        Index(
            "one_active_session_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('printing', 'done_wait')"),
        ),
    )

    def __repr__(self) -> str:
        return f"<MachineSession {self.id} machine={self.machine_id} {self.status}>"


class QueueEntry(Base):
    __tablename__ = "queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # Тип, которого человек ждёт. Очередь на принтеры и очередь на гравировщики
    # независимы: освободившийся гравировщик не должен уходить тому, кому нужна
    # печать, — он бы отказался, а машина простояла бы окно подтверждения.
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{MachineKind.PRINTER}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    offered_machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"))
    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offer_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Когда запись закрылась: занял, окно истекло или вышел сам.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Правило 2: одно место в очереди на человека — тоже на весь парк.
        # Стоять сразу в двух очередях бессмысленно: занять по правилу 2 всё
        # равно получится только что-то одно, а второе приглашение сгорело бы
        # впустую, придержав машину на всё окно подтверждения.
        Index(
            "one_queue_entry_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('waiting', 'offered')"),
        ),
        # Порядок очереди: FIFO по created_at среди активных своего типа.
        Index("queue_active_order", "kind", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<QueueEntry {self.id} user={self.user_id} {self.kind} {self.status}>"
