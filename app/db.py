from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .settings import settings


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    traffic_gb: Mapped[int] = mapped_column(Integer, default=0)
    devices: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    periods: Mapped[list["TariffPeriod"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    squads: Mapped[list["TariffSquad"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class TariffPeriod(Base):
    __tablename__ = "tariff_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    tariff_id: Mapped[int] = mapped_column(ForeignKey("tariffs.id", ondelete="CASCADE"), index=True)
    days: Mapped[int] = mapped_column(Integer)
    price_kopeks: Mapped[int] = mapped_column(Integer)


class TariffSquad(Base):
    __tablename__ = "tariff_squads"

    id: Mapped[int] = mapped_column(primary_key=True)
    tariff_id: Mapped[int] = mapped_column(ForeignKey("tariffs.id", ondelete="CASCADE"), index=True)
    squad_uuid: Mapped[str] = mapped_column(String(36), index=True)


class SquadCache(Base):
    __tablename__ = "squad_cache"

    uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class LoginCode(Base):
    __tablename__ = "login_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(Integer, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(Integer, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Customer(Base):
    __tablename__ = "customers"

    telegram_id: Mapped[int] = mapped_column(primary_key=True)
    referred_by: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    first_paid_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bonus_balance_kopeks: Mapped[int] = mapped_column(Integer, default=0)
    bonus_reserved_kopeks: Mapped[int] = mapped_column(Integer, default=0)
    remna_username: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    subscription_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.telegram_id"), index=True)
    tariff_id: Mapped[int] = mapped_column(ForeignKey("tariffs.id"))
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    days: Mapped[int] = mapped_column(Integer)
    price_kopeks: Mapped[int] = mapped_column(Integer)
    bonus_kopeks: Mapped[int] = mapped_column(Integer, default=0)
    cash_kopeks: Mapped[int] = mapped_column(Integer)
    traffic_gb: Mapped[int] = mapped_column(Integer)
    devices: Mapped[int] = mapped_column(Integer)
    squads_csv: Mapped[str] = mapped_column(Text)
    provider_payment_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    payment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BonusEntry(Base):
    __tablename__ = "bonus_entries"
    __table_args__ = (UniqueConstraint("kind", "order_id", name="uq_bonus_kind_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.telegram_id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    kind: Mapped[str] = mapped_column(String(24))
    amount_kopeks: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.telegram_id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    sender_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class BotText(Base):
    __tablename__ = "bot_texts"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class BotMenuButton(Base):
    __tablename__ = "bot_menu_buttons"

    action: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BotUiLabel(Base):
    __tablename__ = "bot_ui_labels"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.get(Tariff, 1) is None and not db.query(Tariff).first():
            trial = Tariff(name="Пробный доступ", description="1 день · 20 ГБ · 1 устройство", traffic_gb=20, devices=1, is_trial=True)
            trial.periods = [TariffPeriod(days=1, price_kopeks=0)]
            db.add(trial)
            db.commit()
        from .bot_content import ensure_bot_content
        ensure_bot_content(db)
