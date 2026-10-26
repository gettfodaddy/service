import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import func, select

from .commerce import aware, cancel_order, customer_for, create_order, provision_trial, reconcile_payment, start_payment
from .db import AuditEvent, Customer, Order, SessionLocal, SupportTicket, Tariff, TicketMessage, now_utc
from .settings import settings
from .bot_content import get_button_appearances, get_menu_buttons, get_text, ui_label
from . import yookassa


router = Router()
log = logging.getLogger(__name__)
TICKET_STATUSES = {"open": "Открыт", "answered": "Есть ответ", "closed": "Закрыт"}


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [styled_button(title, callback_data=data) for title, data in row]
        for row in rows
    ])


def styled_button(title: str, **destination: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=str(title), icon_custom_emoji_id=getattr(title, "icon_custom_emoji_id", None),
                                style=getattr(title, "style", None), **destination)


def main_kb() -> InlineKeyboardMarkup:
    appearances = get_button_appearances()
    buttons = []
    for button in get_menu_buttons():
        appearance = appearances.get(button.action)
        buttons.append(InlineKeyboardButton(
            text=button.label, callback_data=f"u:{button.action}",
            icon_custom_emoji_id=appearance.icon_custom_emoji_id if appearance else None,
            style=appearance.style if appearance else None,
        ))
    return InlineKeyboardMarkup(inline_keyboard=[buttons[index:index + 2] for index in range(0, len(buttons), 2)])


class TicketForm(StatesGroup):
    new_message = State()
    reply_message = State()


@router.message(Command("cancel"))
async def cancel_ticket(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(get_text("action_canceled"), reply_markup=main_kb())


def fmt_rub(kopeks: int) -> str:
    return f"{kopeks / 100:,.2f}".replace(",", " ").replace(".00", "") + " ₽"


@router.message(CommandStart())
async def start(message: Message):
    telegram_id = message.from_user.id
    argument = (message.text or "").split(maxsplit=1)
    referrer = None
    if len(argument) == 2 and argument[1].startswith("ref_"):
        value = argument[1][4:]
        referrer = int(value) if value.isdigit() else None
    customer_for(telegram_id, referrer)
    if len(argument) == 2 and argument[1].startswith("order_"):
        await message.answer(get_text("return_from_payment"))
    await message.answer(get_text("welcome"), reply_markup=main_kb())


@router.callback_query(F.data == "u:home")
async def home(query: CallbackQuery):
    await query.message.edit_text(get_text("home"), reply_markup=main_kb())
    await query.answer()


@router.callback_query(F.data == "u:catalog")
async def catalog(query: CallbackQuery):
    customer_for(query.from_user.id)
    with SessionLocal() as db:
        tariffs = list(db.scalars(select(Tariff).where(Tariff.published.is_(True), Tariff.is_trial.is_(False)).order_by(Tariff.sort_order, Tariff.id)))
        pending = db.scalar(select(Order).where(Order.customer_id == query.from_user.id, Order.status.in_(["new", "pending", "paid"])).order_by(Order.id.desc()).limit(1))
    rows = [[(ui_label("tariff", name=t.name), f"u:tariff:{t.id}")] for t in tariffs]
    if pending:
        rows.append([(ui_label("my_order", order_id=pending.id), f"u:order:{pending.id}")])
    rows.append([(ui_label("back_home"), "u:home")])
    await query.message.edit_text(get_text("catalog"), reply_markup=kb(rows))
    await query.answer()


@router.callback_query(F.data.startswith("u:tariff:"))
async def tariff_page(query: CallbackQuery):
    tariff_id = int(query.data.split(":")[2])
    with SessionLocal() as db:
        tariff = db.get(Tariff, tariff_id)
        if not tariff or not tariff.published or tariff.is_trial:
            return await query.answer(get_text("tariff_unavailable"), show_alert=True)
        periods = sorted(tariff.periods, key=lambda p: p.days)
        text = get_text("tariff_detail", name=tariff.name, description=tariff.description or "",
                        traffic=str(tariff.traffic_gb) + " ГБ" if tariff.traffic_gb else "безлимит",
                        devices=tariff.devices)
        rows = [[(ui_label("tariff_period", days=p.days, price=fmt_rub(p.price_kopeks)), f"u:buy:{tariff_id}:{p.days}")] for p in periods]
    rows.append([(ui_label("back_catalog"), "u:catalog")])
    await query.message.edit_text(text, reply_markup=kb(rows))
    await query.answer()


@router.callback_query(F.data.startswith("u:buy:"))
async def buy(query: CallbackQuery):
    if not yookassa.ready():
        return await query.answer(get_text("payment_unconfigured"), show_alert=True)
    _, _, tariff_id, days = query.data.split(":")
    try:
        order = create_order(query.from_user.id, int(tariff_id), int(days))
        order = await start_payment(order.id)
    except ValueError as exc:
        return await query.answer(str(exc), show_alert=True)
    except Exception as exc:
        log.exception("Could not create payment")
        with SessionLocal.begin() as db:
            db.add(AuditEvent(admin_id=0, action="payment.failed", detail=f"Клиент {query.from_user.id} · {type(exc).__name__}"))
        return await query.message.answer(get_text("payment_create_failed"))
    await render_order(query, order)


async def render_order(query: CallbackQuery, order: Order):
    text = get_text("order", order_id=order.id, price=fmt_rub(order.price_kopeks),
                    bonus=fmt_rub(order.bonus_kopeks), cash=fmt_rub(order.cash_kopeks), status=order.status)
    rows = []
    if order.payment_url and order.status == "pending":
        rows.append([styled_button(ui_label("pay"), url=order.payment_url)])
        rows.append([styled_button(ui_label("check_payment"), callback_data=f"u:check:{order.id}")])
        rows.append([styled_button(ui_label("cancel_order"), callback_data=f"u:cancel-order:{order.id}")])
    elif order.status == "paid":
        rows.append([styled_button(ui_label("finish_activation"), callback_data=f"u:check:{order.id}")])
    rows.append([styled_button(ui_label("back_catalog"), callback_data="u:catalog")])
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
    finally:
        await query.answer()


@router.callback_query(F.data.startswith("u:order:"))
async def order_page(query: CallbackQuery):
    with SessionLocal() as db:
        order = db.get(Order, int(query.data.split(":")[2]))
        if not order or order.customer_id != query.from_user.id:
            return await query.answer(get_text("order_not_found"), show_alert=True)
    if order.status == "new":
        try:
            order = await start_payment(order.id)
        except Exception:
            log.exception("Could not resume payment")
            return await query.answer(get_text("payment_resume_failed"), show_alert=True)
    await render_order(query, order)


@router.callback_query(F.data.startswith("u:check:"))
async def check_payment(query: CallbackQuery):
    order_id = int(query.data.split(":")[2])
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if not order or order.customer_id != query.from_user.id:
            return await query.answer(get_text("order_not_found"), show_alert=True)
    try:
        order = await (reconcile_payment(order_id) if order.provider_payment_id else start_payment(order_id))
    except Exception:
        log.exception("Payment reconciliation failed")
        return await query.answer(get_text("payment_check_failed"), show_alert=True)
    await render_order(query, order)


@router.callback_query(F.data.startswith("u:cancel-order:"))
async def cancel_payment_order(query: CallbackQuery):
    order_id = int(query.data.split(":")[2])
    try:
        order = await cancel_order(order_id, query.from_user.id)
    except Exception:
        log.exception("Could not cancel order")
        return await query.answer(get_text("payment_cancel_failed"), show_alert=True)
    await render_order(query, order)


@router.callback_query(F.data == "u:trial")
async def trial(query: CallbackQuery):
    customer_for(query.from_user.id)
    try:
        customer = await provision_trial(query.from_user.id)
    except ValueError as exc:
        return await query.answer(str(exc), show_alert=True)
    except Exception as exc:
        log.exception("Trial provisioning failed")
        with SessionLocal.begin() as db:
            db.add(AuditEvent(admin_id=0, action="trial.failed", detail=f"Клиент {query.from_user.id} · {type(exc).__name__}"))
        return await query.answer(get_text("trial_failed"), show_alert=True)
    await query.message.edit_text(
        get_text("trial_success", subscription_url=customer.subscription_url or 'Откройте «Мой ключ» чуть позже'),
        reply_markup=kb([[ (ui_label("open_key"), "u:key") ], [(ui_label("back_home"), "u:home")]]),
    )
    await query.answer()


@router.callback_query(F.data == "u:key")
async def my_key(query: CallbackQuery):
    customer_for(query.from_user.id)
    with SessionLocal() as db:
        customer = db.get(Customer, query.from_user.id)
    if not customer.subscription_url:
        text = get_text("key_empty")
    else:
        expiry = aware(customer.expire_at).strftime("%d.%m.%Y %H:%M UTC") if customer.expire_at else "неизвестно"
        text = get_text("key_active", expiry=expiry, subscription_url=customer.subscription_url)
    await query.message.edit_text(text, reply_markup=kb([[ (ui_label("open_catalog"), "u:catalog") ], [(ui_label("back_home"), "u:home")]]))
    await query.answer()


@router.callback_query(F.data == "u:referrals")
async def referrals(query: CallbackQuery):
    customer_for(query.from_user.id)
    with SessionLocal() as db:
        customer = db.get(Customer, query.from_user.id)
        invited = db.scalar(select(func.count()).select_from(Customer).where(Customer.referred_by == customer.telegram_id)) or 0
    link = f"https://t.me/{settings.bot_username.lstrip('@')}?start=ref_{customer.telegram_id}" if settings.bot_username else "Укажите BOT_USERNAME на сервере"
    await query.message.edit_text(
        get_text("referrals", invited=invited, balance=fmt_rub(customer.bonus_balance_kopeks), referral_link=link),
        reply_markup=kb([[ (ui_label("back_home"), "u:home") ]]),
    )
    await query.answer()


@router.callback_query(F.data == "u:instructions")
async def instructions(query: CallbackQuery):
    text = get_text("instructions")
    await query.message.edit_text(text, reply_markup=kb([[ (ui_label("back_home"), "u:home") ]]))
    await query.answer()


@router.callback_query(F.data == "u:reviews")
async def reviews(query: CallbackQuery):
    await query.message.edit_text(get_text("reviews"), reply_markup=kb([[ (ui_label("back_home"), "u:home") ]]))
    await query.answer()


@router.callback_query(F.data == "u:rules")
async def rules(query: CallbackQuery):
    await query.message.edit_text(get_text("rules"), reply_markup=kb([[ (ui_label("back_home"), "u:home") ]]))
    await query.answer()


@router.callback_query(F.data == "u:support")
async def support(query: CallbackQuery):
    customer_for(query.from_user.id)
    with SessionLocal() as db:
        tickets = list(db.scalars(select(SupportTicket).where(SupportTicket.customer_id == query.from_user.id).order_by(SupportTicket.id.desc()).limit(5)))
    rows = [[(ui_label("ticket", ticket_id=t.id, status=TICKET_STATUSES.get(t.status, t.status)), f"u:ticket:{t.id}")] for t in tickets]
    rows += [[(ui_label("new_ticket"), "u:ticket-new")], [(ui_label("back_home"), "u:home")]]
    await query.message.edit_text(get_text("support"), reply_markup=kb(rows))
    await query.answer()


@router.callback_query(F.data == "u:ticket-new")
async def ticket_new(query: CallbackQuery, state: FSMContext):
    await state.set_state(TicketForm.new_message)
    await query.message.answer(get_text("ticket_new_prompt"))
    await query.answer()


@router.message(TicketForm.new_message)
async def ticket_new_message(message: Message, state: FSMContext):
    content = (message.text or "").strip()
    if not 1 <= len(content) <= 4000:
        return await message.answer(get_text("ticket_invalid_length"))
    with SessionLocal.begin() as db:
        ticket = SupportTicket(customer_id=message.from_user.id)
        db.add(ticket)
        db.flush()
        db.add(TicketMessage(ticket_id=ticket.id, sender_id=message.from_user.id, text=content))
        db.add(AuditEvent(admin_id=0, action="ticket.created", entity_id=ticket.id, detail=f"Клиент {message.from_user.id}"))
        ticket_id = ticket.id
    await state.clear()
    await message.answer(get_text("ticket_created", ticket_id=ticket_id), reply_markup=main_kb())
    for admin_id in settings.admins:
        try:
            await message.bot.send_message(admin_id, f"💬 Новый тикет #{ticket_id}\nОт: {message.from_user.id}\n{content[:1000]}\n\nОткройте /tickets")
        except Exception:
            log.exception("Could not notify admin about ticket")


@router.callback_query(F.data.startswith("u:ticket:"))
async def ticket_page(query: CallbackQuery):
    ticket_id = int(query.data.split(":")[2])
    with SessionLocal() as db:
        ticket = db.get(SupportTicket, ticket_id)
        if not ticket or ticket.customer_id != query.from_user.id:
            return await query.answer(get_text("ticket_not_found"), show_alert=True)
        messages = list(db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.id.desc()).limit(8)))
    lines = [f"{'Вы' if m.sender_id == query.from_user.id else 'Поддержка'}: {m.text[:500]}" for m in reversed(messages)]
    await query.message.edit_text(f"💬 Тикет #{ticket_id} · {TICKET_STATUSES.get(ticket.status, ticket.status)}\n\n" + "\n\n".join(lines), reply_markup=kb([[ (ui_label("reply_ticket"), f"u:ticket-reply:{ticket_id}") ], [(ui_label("back_support"), "u:support")]]))
    await query.answer()


@router.callback_query(F.data.startswith("u:ticket-reply:"))
async def ticket_reply(query: CallbackQuery, state: FSMContext):
    ticket_id = int(query.data.split(":")[2])
    with SessionLocal() as db:
        ticket = db.get(SupportTicket, ticket_id)
        if not ticket or ticket.customer_id != query.from_user.id:
            return await query.answer(get_text("ticket_not_found"), show_alert=True)
    await state.update_data(ticket_id=ticket_id)
    await state.set_state(TicketForm.reply_message)
    await query.message.answer(get_text("ticket_reply_prompt", ticket_id=ticket_id))
    await query.answer()


@router.message(TicketForm.reply_message)
async def ticket_reply_message(message: Message, state: FSMContext):
    data = await state.get_data()
    content = (message.text or "").strip()
    if not 1 <= len(content) <= 4000:
        return await message.answer(get_text("ticket_invalid_length"))
    with SessionLocal.begin() as db:
        ticket = db.get(SupportTicket, data["ticket_id"])
        if not ticket or ticket.customer_id != message.from_user.id:
            return await message.answer(get_text("ticket_not_found"))
        ticket.status = "open"
        ticket.updated_at = now_utc()
        db.add(TicketMessage(ticket_id=ticket.id, sender_id=message.from_user.id, text=content))
        db.add(AuditEvent(admin_id=0, action="ticket.customer_replied", entity_id=ticket.id, detail=f"Клиент {message.from_user.id}"))
    await state.clear()
    await message.answer(get_text("ticket_reply_sent"), reply_markup=main_kb())
    for admin_id in settings.admins:
        try:
            await message.bot.send_message(admin_id, f"💬 Новый ответ по тикету #{ticket.id}\nОт: {message.from_user.id}\n{content[:1000]}\n\nОткройте /tickets")
        except Exception:
            log.exception("Could not notify admin about ticket reply")
