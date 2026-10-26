import asyncio
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from .api import digest
from .db import AuditEvent, LoginCode, SessionLocal, SquadCache, Tariff, TariffPeriod, TariffSquad, init_db, now_utc
from .remnawave import fetch_internal_squads
from .settings import settings


router = Router()


def permitted(user_id: int | None) -> bool:
    return user_id is not None and user_id in settings.admins


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


class Form(StatesGroup):
    create_name = State()
    create_traffic = State()
    create_devices = State()
    create_periods = State()
    edit_value = State()


@router.message(CommandStart())
async def start(message: Message):
    await message.answer(f"🌐 {settings.app_name}\n\nЗдесь появятся покупка, пробный доступ и управление подпиской. Администратору доступна команда /admin.")


@router.message(Command("admin"))
async def admin_menu(message: Message):
    if not permitted(message.from_user.id if message.from_user else None):
        return
    await message.answer("🛡 Панель администратора\n\nТарифы и сквады используют ту же базу, что и сайт.", reply_markup=keyboard([
        [("📦 Тарифы", "a:tariffs"), ("🌐 Сквады", "a:squads")],
        [("🔑 Код для сайта", "a:login")],
    ]))


@router.callback_query(F.data == "a:login")
async def login_code(query: CallbackQuery):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    if not settings.app_secret or len(settings.app_secret) < 24:
        return await query.answer("Настройте APP_SECRET", show_alert=True)
    code = f"{secrets.randbelow(100_000_000):08d}"
    with SessionLocal() as db:
        db.add(LoginCode(admin_id=query.from_user.id, code_hash=digest(code), expires_at=now_utc() + timedelta(minutes=5)))
        db.commit()
    await query.message.answer(f"🔑 Одноразовый код для входа на сайт: {code}\nДействует 5 минут. Не пересылайте его.")
    await query.answer()


@router.callback_query(F.data == "a:tariffs")
async def list_tariffs(query: CallbackQuery):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    with SessionLocal() as db:
        items = list(db.scalars(select(Tariff).order_by(Tariff.sort_order, Tariff.id)))
    rows = [[(f"{'🟢' if t.published else '⚪'} {t.name}", f"a:tariff:{t.id}")] for t in items]
    rows += [[("＋ Новый тариф", "a:new")], [("← Назад", "a:home")]]
    await query.message.edit_text("📦 Тарифы\n\nСоздавайте черновики, настраивайте цену и сквады, затем публикуйте.", reply_markup=keyboard(rows))
    await query.answer()


@router.callback_query(F.data == "a:home")
async def home(query: CallbackQuery):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    await query.message.edit_text("🛡 Панель администратора", reply_markup=keyboard([
        [("📦 Тарифы", "a:tariffs"), ("🌐 Сквады", "a:squads")],
        [("🔑 Код для сайта", "a:login")],
    ]))
    await query.answer()


async def show_tariff(query: CallbackQuery, tariff_id: int):
    with SessionLocal() as db:
        row = db.get(Tariff, tariff_id)
        if not row:
            return await query.answer("Тариф не найден", show_alert=True)
        squad_names = {s.uuid: s.name for s in db.scalars(select(SquadCache))}
        periods = ", ".join(f"{p.days} д — {p.price_kopeks / 100:g} ₽" for p in sorted(row.periods, key=lambda p: p.days))
        squads = ", ".join(squad_names.get(s.squad_uuid, "⚠️ отсутствует") for s in row.squads) or "не выбраны"
        text = (f"📦 {row.name}\n{row.description or 'Без описания'}\n\n"
                f"Статус: {'опубликован' if row.published else 'черновик'}\n"
                f"Трафик: {'безлимит' if row.traffic_gb == 0 else str(row.traffic_gb) + ' ГБ'}\n"
                f"Устройств: {row.devices}\nПериоды: {periods}\nСквады: {squads}")
        toggle = "Скрыть" if row.published else "Опубликовать"
    await query.message.edit_text(text, reply_markup=keyboard([
        [("Название", f"a:edit:{tariff_id}:name"), ("Описание", f"a:edit:{tariff_id}:description")],
        [("Трафик", f"a:edit:{tariff_id}:traffic"), ("Устройства", f"a:edit:{tariff_id}:devices")],
        [("Цены", f"a:edit:{tariff_id}:periods"), ("Сквады", f"a:choose:{tariff_id}")],
        [(toggle, f"a:toggle:{tariff_id}")],
        [("← Тарифы", "a:tariffs")],
    ]))
    await query.answer()


@router.callback_query(F.data.startswith("a:tariff:"))
async def tariff_page(query: CallbackQuery):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    await show_tariff(query, int(query.data.split(":")[2]))


@router.callback_query(F.data == "a:new")
async def new_tariff(query: CallbackQuery, state: FSMContext):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    await state.set_state(Form.create_name)
    await query.message.answer("Введите название нового тарифа (2–60 символов). /cancel — отмена.")
    await query.answer()


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено. /admin")


@router.message(Form.create_name)
async def create_name(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not 2 <= len(value) <= 60:
        return await message.answer("Название должно быть длиной 2–60 символов.")
    await state.update_data(name=value)
    await state.set_state(Form.create_traffic)
    await message.answer("Лимит трафика в ГБ. 0 — безлимит.")


@router.message(Form.create_traffic)
async def create_traffic(message: Message, state: FSMContext):
    if not (message.text or "").isdigit():
        return await message.answer("Введите целое число от 0.")
    await state.update_data(traffic=int(message.text))
    await state.set_state(Form.create_devices)
    await message.answer("Количество устройств (минимум 1).")


@router.message(Form.create_devices)
async def create_devices(message: Message, state: FSMContext):
    if not (message.text or "").isdigit() or int(message.text) < 1:
        return await message.answer("Введите целое число от 1.")
    await state.update_data(devices=int(message.text))
    await state.set_state(Form.create_periods)
    await message.answer("Периоды и цены в рублях: 30:299, 90:799, 180:1399")


def parse_periods(value: str) -> list[TariffPeriod]:
    result = []
    for part in value.split(","):
        days, price = part.strip().split(":", 1)
        try:
            amount = Decimal(price.strip().replace(",", "."))
            kopeks = int(amount * 100)
            if amount * 100 != kopeks:
                raise ValueError("Too many decimal places")
        except InvalidOperation as exc:
            raise ValueError("Invalid price") from exc
        if not days.strip().isdigit() or not 1 <= int(days) <= 366 or kopeks <= 0:
            raise ValueError("Invalid period")
        result.append(TariffPeriod(days=int(days), price_kopeks=kopeks))
    if not result or len({p.days for p in result}) != len(result):
        raise ValueError("Duplicate or empty periods")
    return result


@router.message(Form.create_periods)
async def create_periods(message: Message, state: FSMContext):
    try:
        periods = parse_periods(message.text or "")
    except (ValueError, TypeError):
        return await message.answer("Формат: 30:299, 90:799. Цена должна быть больше нуля.")
    data = await state.get_data()
    with SessionLocal() as db:
        row = Tariff(name=data["name"], traffic_gb=data["traffic"], devices=data["devices"])
        row.periods = periods
        db.add(row)
        db.flush()
        row_id = row.id
        db.add(AuditEvent(admin_id=message.from_user.id, action="tariff.created", entity_id=row_id, detail=row.name))
        db.commit()
    await state.clear()
    await message.answer(f"Черновик #{row_id} создан. Выберите сквады и опубликуйте.", reply_markup=keyboard([[ ("Открыть тариф", f"a:tariff:{row_id}") ]]))


@router.callback_query(F.data.startswith("a:edit:"))
async def edit_field(query: CallbackQuery, state: FSMContext):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    _, _, tariff_id, field = query.data.split(":")
    if field not in {"name", "description", "traffic", "devices", "periods"}:
        return await query.answer("Неизвестное поле", show_alert=True)
    await state.update_data(tariff_id=int(tariff_id), field=field)
    await state.set_state(Form.edit_value)
    await query.message.answer(f"Новое значение поля {field}. Для цен: 30:299, 90:799. /cancel — отмена.")
    await query.answer()


@router.message(Form.edit_value)
async def edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data["field"]
    value = (message.text or "").strip()
    try:
        with SessionLocal() as db:
            row = db.get(Tariff, data["tariff_id"])
            if not row:
                raise ValueError("Тариф не найден")
            if field == "name":
                if not 2 <= len(value) <= 60:
                    raise ValueError("Название: 2–60 символов")
                row.name = value
            elif field == "description":
                row.description = value[:1000]
            elif field == "traffic":
                if row.is_trial:
                    raise ValueError("Пробный тариф фиксирован: 20 ГБ")
                if not value.isdigit():
                    raise ValueError("Введите число от 0")
                row.traffic_gb = int(value)
            elif field == "devices":
                if row.is_trial:
                    raise ValueError("Пробный тариф фиксирован: 1 устройство")
                if not value.isdigit() or int(value) < 1:
                    raise ValueError("Введите число от 1")
                row.devices = int(value)
            elif field == "periods":
                if row.is_trial:
                    raise ValueError("У пробного тарифа период фиксирован")
                row.periods = parse_periods(value)
            db.add(AuditEvent(admin_id=message.from_user.id, action=f"tariff.{field}.updated", entity_id=row.id))
            db.commit()
    except ValueError as exc:
        return await message.answer(str(exc))
    await state.clear()
    await message.answer("Сохранено. Новые условия применятся к следующим покупкам и продлениям.", reply_markup=keyboard([[ ("Открыть тариф", f"a:tariff:{data['tariff_id']}") ]]))


async def render_squad_picker(query: CallbackQuery, tariff_id: int):
    with SessionLocal() as db:
        row = db.get(Tariff, tariff_id)
        if not row:
            return await query.answer("Тариф не найден", show_alert=True)
        chosen = {s.squad_uuid for s in row.squads}
        squads = list(db.scalars(select(SquadCache).where(SquadCache.present.is_(True)).order_by(SquadCache.name)))
        rows = [[(f"{'✅' if s.uuid in chosen else '▫️'} {s.name}", f"a:squadtoggle:{tariff_id}:{s.uuid}")] for s in squads[:40]]
    rows.append([("← К тарифу", f"a:tariff:{tariff_id}")])
    await query.message.edit_text("Выберите внутренние сквады Remnawave для тарифа. Если список пуст, нажмите «Сквады» в /admin и синхронизируйте.", reply_markup=keyboard(rows))
    await query.answer()


@router.callback_query(F.data.startswith("a:choose:"))
async def choose_squads(query: CallbackQuery):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    await render_squad_picker(query, int(query.data.split(":")[2]))


@router.callback_query(F.data.startswith("a:squadtoggle:"))
async def toggle_squad(query: CallbackQuery):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    _, _, tariff_id_raw, uuid = query.data.split(":")
    tariff_id = int(tariff_id_raw)
    with SessionLocal() as db:
        row = db.get(Tariff, tariff_id)
        squad = db.get(SquadCache, uuid)
        if not row or not squad or not squad.present:
            return await query.answer("Сквад не найден", show_alert=True)
        existing = next((s for s in row.squads if s.squad_uuid == uuid), None)
        if existing:
            if row.published and len(row.squads) == 1:
                return await query.answer("У опубликованного тарифа нужен хотя бы один сквад", show_alert=True)
            row.squads.remove(existing)
        else:
            row.squads.append(TariffSquad(squad_uuid=uuid))
        db.add(AuditEvent(admin_id=query.from_user.id, action="tariff.squads.updated", entity_id=tariff_id, detail=uuid))
        db.commit()
    await render_squad_picker(query, tariff_id)


@router.callback_query(F.data.startswith("a:toggle:"))
async def toggle_publish(query: CallbackQuery):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    tariff_id = int(query.data.split(":")[2])
    with SessionLocal() as db:
        row = db.get(Tariff, tariff_id)
        if not row:
            return await query.answer("Тариф не найден", show_alert=True)
        if not row.published:
            valid = {s.uuid for s in db.scalars(select(SquadCache).where(SquadCache.present.is_(True)))}
            if not row.squads or any(s.squad_uuid not in valid for s in row.squads):
                return await query.answer("Выберите существующие сквады", show_alert=True)
            if not row.is_trial and (not row.periods or any(p.price_kopeks <= 0 for p in row.periods)):
                return await query.answer("Задайте цены", show_alert=True)
        row.published = not row.published
        db.add(AuditEvent(admin_id=query.from_user.id, action="tariff.publication.updated", entity_id=tariff_id, detail=str(row.published)))
        db.commit()
    await show_tariff(query, tariff_id)


async def render_squads(query: CallbackQuery):
    with SessionLocal() as db:
        squads = list(db.scalars(select(SquadCache).order_by(SquadCache.name)))
    lines = [f"{'🟢' if s.present else '⚠️'} {s.name}" for s in squads]
    await query.message.edit_text("🌐 Внутренние сквады Remnawave\n\n" + ("\n".join(lines[:40]) or "Пока не загружены"), reply_markup=keyboard([
        [("↻ Синхронизировать", "a:sync")], [("← Назад", "a:home")]
    ]))
    await query.answer()


@router.callback_query(F.data == "a:squads")
async def squads_page(query: CallbackQuery):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    await render_squads(query)


@router.callback_query(F.data == "a:sync")
async def sync_squads(query: CallbackQuery):
    if not permitted(query.from_user.id):
        return await query.answer("Нет доступа", show_alert=True)
    try:
        remote = await fetch_internal_squads()
    except Exception as exc:
        return await query.answer(f"Ошибка Remnawave: {type(exc).__name__}", show_alert=True)
    with SessionLocal() as db:
        for row in db.scalars(select(SquadCache)):
            row.present = False
        for item in remote:
            row = db.get(SquadCache, item["uuid"]) or SquadCache(uuid=item["uuid"])
            row.name = item["name"]
            row.present = True
            row.synced_at = now_utc()
            db.add(row)
        db.add(AuditEvent(admin_id=query.from_user.id, action="squads.synced", detail=str(len(remote))))
        db.commit()
    await render_squads(query)


async def main():
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    init_db()
    bot = Bot(settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
