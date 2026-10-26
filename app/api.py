import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from aiogram import Bot
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import AuditEvent, BotButtonAppearance, BotMenuButton, BotText, BotUiLabel, Customer, LoginCode, Order, SessionLocal, SquadCache, SupportTicket, Tariff, TariffPeriod, TariffSquad, TicketMessage, WebSession, init_db, now_utc
from .bot_content import ALERT_TEXTS, MENU_BUTTONS, SYSTEM_TEXTS, TEXTS, UI_LABELS, get_text, validate_label, validate_text
from .commerce import reconcile_payment
from . import yookassa
from .remnawave import fetch_internal_squads
from .settings import settings


STATIC = Path(__file__).parent / "static"
app = FastAPI(title="VPN Admin API", docs_url=None, redoc_url=None)
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


@app.on_event("startup")
def startup() -> None:
    init_db()


def get_db():
    with SessionLocal() as db:
        yield db


DB = Annotated[Session, Depends(get_db)]


def digest(value: str) -> str:
    return hmac.new(settings.app_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def admin(request: Request, db: DB) -> int:
    token = request.cookies.get("admin_session", "")
    if not token or not settings.app_secret:
        raise HTTPException(401, "Войдите через Telegram")
    row = db.scalar(select(WebSession).where(WebSession.token_hash == digest(token)))
    if not row or utc(row.expires_at) <= now_utc() or row.admin_id not in settings.admins:
        raise HTTPException(401, "Сессия истекла")
    origin = request.headers.get("Origin")
    if origin and origin != settings.web_origin:
        raise HTTPException(403, "Неверный Origin")
    if request.method not in {"GET", "HEAD"} and request.headers.get("X-CSRF-Token") != row.csrf_token:
        raise HTTPException(403, "Неверный CSRF-токен")
    return row.admin_id


ADMIN = Annotated[int, Depends(admin)]


class LoginIn(BaseModel):
    code: str = Field(pattern=r"^\d{8}$")


class PeriodIn(BaseModel):
    days: int = Field(ge=1, le=366)
    price_kopeks: int = Field(ge=0)


class TariffIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    description: str = Field(default="", max_length=1000)
    traffic_gb: int = Field(ge=0)
    devices: int = Field(ge=1)
    sort_order: int = 0
    published: bool = False
    is_trial: bool = False
    periods: list[PeriodIn] = Field(min_length=1)
    squad_uuids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check(self):
        if len({period.days for period in self.periods}) != len(self.periods):
            raise ValueError("Повторяющийся период")
        if not self.is_trial and any(period.price_kopeks < 100 for period in self.periods):
            raise ValueError("Цена платного тарифа должна быть не ниже 1 ₽")
        if self.published and not self.squad_uuids:
            raise ValueError("Для публикации выберите хотя бы один сквад")
        if len(set(self.squad_uuids)) != len(self.squad_uuids):
            raise ValueError("Сквады не должны повторяться")
        if self.is_trial and (self.traffic_gb != 20 or self.devices != 1 or len(self.periods) != 1 or self.periods[0].days != 1 or self.periods[0].price_kopeks != 0):
            raise ValueError("Пробный тариф фиксирован: 1 день, 20 ГБ, 1 устройство, 0 ₽")
        return self


def tariff_data(row: Tariff) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "traffic_gb": row.traffic_gb,
        "devices": row.devices,
        "sort_order": row.sort_order,
        "published": row.published,
        "is_trial": row.is_trial,
        "periods": [{"days": p.days, "price_kopeks": p.price_kopeks} for p in sorted(row.periods, key=lambda p: p.days)],
        "squad_uuids": [s.squad_uuid for s in row.squads],
    }


def save_tariff(db: Session, row: Tariff, payload: TariffIn, admin_id: int, action: str) -> Tariff:
    known = {s.uuid for s in db.scalars(select(SquadCache).where(SquadCache.present.is_(True)))}
    if not set(payload.squad_uuids) <= known:
        raise HTTPException(400, "Сначала синхронизируйте сквады; выбранный сквад не найден")
    if payload.is_trial:
        other = db.scalar(select(Tariff).where(Tariff.is_trial.is_(True), Tariff.id != (row.id or 0)))
        if other:
            raise HTTPException(400, "Пробный тариф уже существует")
    row.name = payload.name
    row.description = payload.description
    row.traffic_gb = payload.traffic_gb
    row.devices = payload.devices
    row.sort_order = payload.sort_order
    row.published = payload.published
    row.is_trial = payload.is_trial
    row.periods = [TariffPeriod(days=p.days, price_kopeks=p.price_kopeks) for p in payload.periods]
    row.squads = [TariffSquad(squad_uuid=uuid) for uuid in payload.squad_uuids]
    db.add(row)
    db.flush()
    db.add(AuditEvent(admin_id=admin_id, action=action, entity_id=row.id,
                      detail=f"{row.name} · {row.traffic_gb or '∞'} ГБ · {row.devices} устр. · {len(row.periods)} периодов · {len(row.squads)} сквадов · {'опубликован' if row.published else 'черновик'}"))
    db.commit()
    db.refresh(row)
    return row


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/public-config")
def public_config():
    return {"app_name": get_text("brand_name")}


@app.post("/api/yookassa/webhook")
async def yookassa_webhook(request: Request):
    payload = await request.json()
    if payload.get("type") != "notification" or payload.get("event") not in {"payment.succeeded", "payment.canceled"}:
        return {"ok": True}
    payment = payload.get("object") or {}
    payment_id = payment.get("id")
    if not isinstance(payment_id, str):
        raise HTTPException(400, "Missing payment ID")
    with SessionLocal() as db:
        order = db.scalar(select(Order).where(Order.provider_payment_id == payment_id))
    if not order:
        # A fast notification can arrive before create_payment() stores its ID.
        verified = await yookassa.get_payment(payment_id)
        claimed_id = str(verified.get("metadata", {}).get("order_id", ""))
        if not claimed_id.isdigit():
            return {"ok": True}
        with SessionLocal.begin() as db:
            order = db.scalar(select(Order).where(Order.id == int(claimed_id)).with_for_update())
            if not order or order.provider_payment_id not in (None, payment_id):
                return {"ok": True}
            order.provider_payment_id = payment_id
    order_id, customer_id, prior_status = order.id, order.customer_id, order.status
    # Never trust a notification body as proof of payment. Read the actual
    # payment from YooKassa with our own credentials before provisioning.
    try:
        result = await reconcile_payment(order_id)
    except Exception as exc:
        with SessionLocal.begin() as db:
            db.add(AuditEvent(admin_id=0, action="payment.reconciliation_failed", entity_id=order_id,
                              detail=f"Клиент {customer_id} · {type(exc).__name__}"))
        raise HTTPException(503, "Payment reconciliation will be retried") from exc
    if prior_status != "complete" and result.status == "complete" and settings.bot_token:
        bot = Bot(settings.bot_token)
        try:
            await bot.send_message(customer_id, get_text("payment_success"))
        except Exception:
            pass
        finally:
            await bot.session.close()
    return {"ok": True}


@app.post("/api/login")
def login(payload: LoginIn, request: Request, response: Response, db: DB):
    origin = request.headers.get("Origin")
    if origin and origin != settings.web_origin:
        raise HTTPException(403, "Неверный Origin")
    if not settings.app_secret or len(settings.app_secret) < 24:
        raise HTTPException(503, "APP_SECRET не настроен")
    row = db.scalar(select(LoginCode).where(LoginCode.code_hash == digest(payload.code), LoginCode.used_at.is_(None)))
    if not row or utc(row.expires_at) <= now_utc() or row.admin_id not in settings.admins:
        raise HTTPException(401, "Неверный или просроченный код")
    row.used_at = now_utc()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    db.add(WebSession(admin_id=row.admin_id, token_hash=digest(token), csrf_token=csrf, expires_at=now_utc() + timedelta(days=7)))
    db.add(AuditEvent(admin_id=row.admin_id, action="auth.login", detail="Вход в веб-панель"))
    db.commit()
    response.set_cookie("admin_session", token, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=604800)
    return {"csrf_token": csrf}


@app.get("/api/me")
def me(request: Request, db: DB, admin_id: ADMIN):
    token = request.cookies["admin_session"]
    row = db.scalar(select(WebSession).where(WebSession.token_hash == digest(token)))
    return {"admin_id": admin_id, "csrf_token": row.csrf_token}


@app.post("/api/logout")
def logout(request: Request, response: Response, db: DB, admin_id: ADMIN):
    token = request.cookies["admin_session"]
    row = db.scalar(select(WebSession).where(WebSession.token_hash == digest(token)))
    db.delete(row)
    db.add(AuditEvent(admin_id=admin_id, action="auth.logout", detail="Выход из веб-панели"))
    db.commit()
    response.delete_cookie("admin_session")
    return {"ok": True}


@app.get("/api/overview")
def overview(db: DB, admin_id: ADMIN):
    return {
        "tariffs": db.scalar(select(func.count()).select_from(Tariff)) or 0,
        "published": db.scalar(select(func.count()).select_from(Tariff).where(Tariff.published.is_(True))) or 0,
        "squads": db.scalar(select(func.count()).select_from(SquadCache).where(SquadCache.present.is_(True))) or 0,
        "customers": db.scalar(select(func.count()).select_from(Customer)) or 0,
        "orders": db.scalar(select(func.count()).select_from(Order).where(Order.status == "complete")) or 0,
        "open_tickets": db.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.status == "open")) or 0,
        "events": [event_data(e) for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(8))],
    }


def event_data(event: AuditEvent) -> dict:
    return {"id": event.id, "actor_id": event.admin_id, "action": event.action,
            "entity_id": event.entity_id, "detail": event.detail,
            "created_at": event.created_at.isoformat()}


@app.get("/api/activity")
def activity(db: DB, admin_id: ADMIN, category: str = Query(default="all", pattern="^(all|tariff|squads|ticket|bot|order|trial|payment|auth)$"),
             page: int = Query(default=1, ge=1), limit: int = Query(default=30, ge=1, le=100)):
    statement = select(AuditEvent)
    count = select(func.count()).select_from(AuditEvent)
    if category != "all":
        statement = statement.where(AuditEvent.action.like(category + ".%"))
        count = count.where(AuditEvent.action.like(category + ".%"))
    total = db.scalar(count) or 0
    rows = db.scalars(statement.order_by(AuditEvent.id.desc()).offset((page - 1) * limit).limit(limit))
    return {"items": [event_data(row) for row in rows], "total": total, "page": page, "limit": limit}


class BotButtonIn(BaseModel):
    action: str
    label: str = Field(min_length=1, max_length=64)
    position: int = Field(ge=0, le=7)
    visible: bool
    icon_custom_emoji_id: str | None = None
    style: str | None = None


class ButtonAppearanceIn(BaseModel):
    icon_custom_emoji_id: str | None = None
    style: str | None = None


class BotContentIn(BaseModel):
    texts: dict[str, str]
    buttons: list[BotButtonIn]
    labels: dict[str, str]
    label_appearances: dict[str, ButtonAppearanceIn] = Field(default_factory=dict)


@app.get("/api/bot-content")
def bot_content(db: DB, admin_id: ADMIN):
    text_rows = {row.key: row for row in db.scalars(select(BotText))}
    button_rows = {row.action: row for row in db.scalars(select(BotMenuButton))}
    appearances = {row.action: row for row in db.scalars(select(BotButtonAppearance))}
    label_rows = {row.key: row for row in db.scalars(select(BotUiLabel))}
    return {
        "texts": [{"key": key, "title": title, "body": (text_rows[key].body if key in text_rows else default),
                   "default": default, "variables": sorted(variables), "max_length": 180 if key in ALERT_TEXTS else 3000,
                   "group": "system" if key in SYSTEM_TEXTS else "main"}
                  for key, (title, default, variables) in TEXTS.items()],
        "buttons": [{"action": action, "label": (button_rows[action].label if action in button_rows else label),
                     "position": (button_rows[action].position if action in button_rows else position),
                     "visible": (button_rows[action].visible if action in button_rows else True),
                     "icon_custom_emoji_id": appearances[action].icon_custom_emoji_id if action in appearances else None,
                     "style": appearances[action].style if action in appearances else None,
                     "default": label}
                    for position, (action, label) in enumerate(MENU_BUTTONS)],
        "labels": [{"key": key, "title": title, "label": (label_rows[key].label if key in label_rows else default),
                    "icon_custom_emoji_id": appearances[f"ui:{key}"].icon_custom_emoji_id if f"ui:{key}" in appearances else None,
                    "style": appearances[f"ui:{key}"].style if f"ui:{key}" in appearances else None,
                    "default": default, "variables": sorted(variables)}
                   for key, (title, default, variables) in UI_LABELS.items()],
    }


@app.put("/api/bot-content")
def update_bot_content(payload: BotContentIn, db: DB, admin_id: ADMIN):
    actions = {action for action, _ in MENU_BUTTONS}
    if set(payload.texts) != set(TEXTS) or set(payload.labels) != set(UI_LABELS) or {button.action for button in payload.buttons} != actions or len(payload.buttons) != len(actions):
        raise HTTPException(400, "Передайте полный набор текстов и кнопок")
    if {button.position for button in payload.buttons} != set(range(len(actions))):
        raise HTTPException(400, "Позиции кнопок должны быть уникальными")
    if not any(button.visible for button in payload.buttons):
        raise HTTPException(400, "Оставьте хотя бы одну кнопку главного меню")
    if not set(payload.label_appearances).issubset(UI_LABELS):
        raise HTTPException(400, "Неизвестная кнопка внутри раздела")
    try:
        for key, body in payload.texts.items():
            validate_text(key, body)
        for key, label in payload.labels.items():
            validate_label(key, label)
        for button in payload.buttons:
            if not button.label.strip():
                raise ValueError("Подпись кнопки не может быть пустой")
            if button.icon_custom_emoji_id and (not button.icon_custom_emoji_id.isascii() or not button.icon_custom_emoji_id.isdecimal() or len(button.icon_custom_emoji_id) > 30):
                raise ValueError("ID кастомного эмодзи должен содержать только цифры")
            if button.style not in (None, "", "primary", "success", "danger"):
                raise ValueError("Недопустимый стиль кнопки")
        for appearance in payload.label_appearances.values():
            if appearance.icon_custom_emoji_id and (not appearance.icon_custom_emoji_id.isascii() or not appearance.icon_custom_emoji_id.isdecimal() or len(appearance.icon_custom_emoji_id) > 30):
                raise ValueError("ID кастомного эмодзи должен содержать только цифры")
            if appearance.style not in (None, "", "primary", "success", "danger"):
                raise ValueError("Недопустимый стиль кнопки")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    for key, body in payload.texts.items():
        row = db.get(BotText, key) or BotText(key=key)
        row.body = body.strip()
        db.add(row)
    for button in payload.buttons:
        row = db.get(BotMenuButton, button.action) or BotMenuButton(action=button.action)
        row.label = button.label.strip()
        row.position = button.position
        row.visible = button.visible
        db.add(row)
        appearance = db.get(BotButtonAppearance, button.action) or BotButtonAppearance(action=button.action)
        appearance.icon_custom_emoji_id = button.icon_custom_emoji_id or None
        appearance.style = button.style or None
        db.add(appearance)
    for key, label in payload.labels.items():
        row = db.get(BotUiLabel, key) or BotUiLabel(key=key)
        row.label = label.strip()
        db.add(row)
        if key in payload.label_appearances:
            appearance = db.get(BotButtonAppearance, f"ui:{key}") or BotButtonAppearance(action=f"ui:{key}")
            appearance.icon_custom_emoji_id = payload.label_appearances[key].icon_custom_emoji_id or None
            appearance.style = payload.label_appearances[key].style or None
            db.add(appearance)
    db.add(AuditEvent(admin_id=admin_id, action="bot.content.updated", detail="Главное меню и тексты"))
    db.commit()
    return {"ok": True}


@app.get("/api/tariffs")
def tariffs(db: DB, admin_id: ADMIN):
    return [tariff_data(row) for row in db.scalars(select(Tariff).order_by(Tariff.sort_order, Tariff.id))]


@app.post("/api/tariffs")
def create_tariff(payload: TariffIn, db: DB, admin_id: ADMIN):
    return tariff_data(save_tariff(db, Tariff(), payload, admin_id, "tariff.created"))


@app.put("/api/tariffs/{tariff_id}")
def update_tariff(tariff_id: int, payload: TariffIn, db: DB, admin_id: ADMIN):
    row = db.get(Tariff, tariff_id)
    if not row:
        raise HTTPException(404, "Тариф не найден")
    return tariff_data(save_tariff(db, row, payload, admin_id, "tariff.updated"))


@app.get("/api/squads")
def squads(db: DB, admin_id: ADMIN):
    return [{"uuid": s.uuid, "name": s.name, "present": s.present} for s in db.scalars(select(SquadCache).order_by(SquadCache.name))]


@app.post("/api/squads/sync")
async def sync_squads(db: DB, admin_id: ADMIN):
    try:
        remote = await fetch_internal_squads()
    except Exception as exc:
        raise HTTPException(502, f"Не удалось получить сквады: {type(exc).__name__}") from exc
    for row in db.scalars(select(SquadCache)):
        row.present = False
    for item in remote:
        row = db.get(SquadCache, item["uuid"]) or SquadCache(uuid=item["uuid"])
        row.name = item["name"]
        row.present = True
        row.synced_at = now_utc()
        db.add(row)
    db.add(AuditEvent(admin_id=admin_id, action="squads.synced", detail=str(len(remote))))
    db.commit()
    return {"count": len(remote)}


@app.get("/api/customers")
def customers(db: DB, admin_id: ADMIN):
    rows = db.scalars(select(Customer).order_by(Customer.created_at.desc()).limit(200))
    return [{
        "telegram_id": row.telegram_id, "referred_by": row.referred_by,
        "trial_used": row.trial_used, "balance_kopeks": row.bonus_balance_kopeks,
        "remna_username": row.remna_username,
        "expire_at": row.expire_at.isoformat() if row.expire_at else None,
    } for row in rows]


@app.get("/api/orders")
def orders(db: DB, admin_id: ADMIN):
    rows = db.scalars(select(Order).order_by(Order.id.desc()).limit(200))
    return [{
        "id": row.id, "customer_id": row.customer_id, "status": row.status,
        "days": row.days, "price_kopeks": row.price_kopeks,
        "bonus_kopeks": row.bonus_kopeks, "cash_kopeks": row.cash_kopeks,
        "created_at": row.created_at.isoformat(),
    } for row in rows]


@app.get("/api/tickets")
def tickets(db: DB, admin_id: ADMIN):
    rows = list(db.scalars(select(SupportTicket).order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc()).limit(200)))
    latest = {}
    if rows:
        messages = db.scalars(select(TicketMessage).where(TicketMessage.ticket_id.in_([row.id for row in rows])).order_by(TicketMessage.id))
        latest = {message.ticket_id: message for message in messages}
    return [{
        "id": row.id, "customer_id": row.customer_id, "status": row.status,
        "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
        "preview": latest[row.id].text[:160] if row.id in latest else "",
        "last_sender": latest[row.id].sender_id if row.id in latest else None,
    } for row in rows]


@app.get("/api/tickets/{ticket_id}")
def ticket_detail(ticket_id: int, db: DB, admin_id: ADMIN):
    ticket = db.get(SupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Тикет не найден")
    messages = db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.id))
    return {"id": ticket.id, "customer_id": ticket.customer_id, "status": ticket.status,
            "created_at": ticket.created_at.isoformat(), "updated_at": ticket.updated_at.isoformat(),
            "messages": [{"sender_id": m.sender_id, "text": m.text, "created_at": m.created_at.isoformat()} for m in messages]}


class TicketStatusIn(BaseModel):
    status: str


@app.post("/api/tickets/{ticket_id}/status")
def ticket_status(ticket_id: int, payload: TicketStatusIn, db: DB, admin_id: ADMIN):
    if payload.status not in {"open", "answered", "closed"}:
        raise HTTPException(400, "Недопустимый статус")
    ticket = db.get(SupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Тикет не найден")
    if ticket.status != payload.status:
        old_status = ticket.status
        ticket.status = payload.status
        ticket.updated_at = now_utc()
        db.add(AuditEvent(admin_id=admin_id, action="ticket.status_changed", entity_id=ticket_id,
                          detail=f"{old_status} → {payload.status}"))
        db.commit()
    return {"ok": True, "status": ticket.status}


class TicketReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@app.post("/api/tickets/{ticket_id}/reply")
async def ticket_reply(ticket_id: int, payload: TicketReplyIn, db: DB, admin_id: ADMIN):
    ticket = db.get(SupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Тикет не найден")
    ticket.status = "answered"
    ticket.updated_at = now_utc()
    db.add(TicketMessage(ticket_id=ticket_id, sender_id=admin_id, text=payload.text))
    db.add(AuditEvent(admin_id=admin_id, action="ticket.replied", entity_id=ticket_id,
                      detail=f"Ответ клиенту {ticket.customer_id} · {len(payload.text)} символов"))
    db.commit()
    delivered = False
    if settings.bot_token:
        bot = Bot(settings.bot_token)
        try:
            await bot.send_message(ticket.customer_id, f"💬 Ответ по тикету #{ticket_id}:\n\n{payload.text}")
            delivered = True
        except Exception:
            delivered = False
        finally:
            await bot.session.close()
    if not delivered:
        db.add(AuditEvent(admin_id=0, action="ticket.delivery_failed", entity_id=ticket_id,
                          detail=f"Telegram не доставил ответ клиенту {ticket.customer_id}"))
        db.commit()
    return {"ok": True, "delivered": delivered}
