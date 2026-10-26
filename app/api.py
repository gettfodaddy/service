import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import AuditEvent, LoginCode, SessionLocal, SquadCache, Tariff, TariffPeriod, TariffSquad, WebSession, init_db, now_utc
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
        if not self.is_trial and any(period.price_kopeks <= 0 for period in self.periods):
            raise ValueError("Цена платного тарифа должна быть больше нуля")
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
    db.add(AuditEvent(admin_id=admin_id, action=action, entity_id=row.id, detail=row.name))
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
    return {"app_name": settings.app_name}


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
    db.commit()
    response.delete_cookie("admin_session")
    return {"ok": True}


@app.get("/api/overview")
def overview(db: DB, admin_id: ADMIN):
    return {
        "tariffs": db.scalar(select(func.count()).select_from(Tariff)) or 0,
        "published": db.scalar(select(func.count()).select_from(Tariff).where(Tariff.published.is_(True))) or 0,
        "squads": db.scalar(select(func.count()).select_from(SquadCache).where(SquadCache.present.is_(True))) or 0,
        "events": [{"action": e.action, "detail": e.detail, "created_at": e.created_at.isoformat()} for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(8))],
    }


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
