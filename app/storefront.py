import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import func, select

from .commerce import aware, cancel_order, customer_for, create_order, provision_trial, reconcile_payment, start_payment
from .db import Customer, Order, SessionLocal, SupportTicket, Tariff, TicketMessage
from .settings import settings
from . import yookassa


router = Router()
log = logging.getLogger(__name__)


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=title, callback_data=data) for title, data in row]
        for row in rows
    ])


def main_kb() -> InlineKeyboardMarkup:
    return kb([
        [("📦 Подписка", "u:catalog"), ("🔑 Мой ключ", "u:key")],
        [("🎁 Бесплатный день", "u:trial"), ("👥 Рефералы", "u:referrals")],
        [("📖 Инструкция", "u:instructions"), ("💬 Поддержка", "u:support")],
        [("⭐ Отзывы", "u:reviews"), ("📄 Правила", "u:rules")],
    ])


class TicketForm(StatesGroup):
    new_message = State()
    reply_message = State()


@router.message(Command("cancel"))
async def cancel_ticket(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_kb())


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
        await message.answer("Проверить оплату можно через «Подписка» → «Мой заказ». Подтверждение от ЮKassa также придёт автоматически.")
    await message.answer(
        f"🌐 {settings.app_name}\n\n"
        "🚀 Подключение за пару минут:\n"
        "1️⃣ Выберите подписку или получите пробный день.\n"
        "2️⃣ Откройте «Мой ключ» и скопируйте ссылку.\n"
        "3️⃣ Добавьте её в VPN-приложение.\n\n"
        "Если нужна помощь — напишите нам через «Поддержку».",
        reply_markup=main_kb(),
    )


@router.callback_query(F.data == "u:home")
async def home(query: CallbackQuery):
    await query.message.edit_text(f"🌐 {settings.app_name}\n\nВыберите раздел:", reply_markup=main_kb())
    await query.answer()


@router.callback_query(F.data == "u:catalog")
async def catalog(query: CallbackQuery):
    customer_for(query.from_user.id)
    with SessionLocal() as db:
        tariffs = list(db.scalars(select(Tariff).where(Tariff.published.is_(True), Tariff.is_trial.is_(False)).order_by(Tariff.sort_order, Tariff.id)))
        pending = db.scalar(select(Order).where(Order.customer_id == query.from_user.id, Order.status.in_(["new", "pending", "paid"])).order_by(Order.id.desc()).limit(1))
    rows = [[(f"📦 {t.name}", f"u:tariff:{t.id}")] for t in tariffs]
    if pending:
        rows.append([(f"💳 Мой заказ №{pending.id}", f"u:order:{pending.id}")])
    rows.append([("← Меню", "u:home")])
    await query.message.edit_text("📦 Подписки\n\nВыберите тариф. Цена и условия сохраняются в заказе на момент оформления.", reply_markup=kb(rows))
    await query.answer()


@router.callback_query(F.data.startswith("u:tariff:"))
async def tariff_page(query: CallbackQuery):
    tariff_id = int(query.data.split(":")[2])
    with SessionLocal() as db:
        tariff = db.get(Tariff, tariff_id)
        if not tariff or not tariff.published or tariff.is_trial:
            return await query.answer("Тариф недоступен", show_alert=True)
        periods = sorted(tariff.periods, key=lambda p: p.days)
        text = (f"📦 {tariff.name}\n{tariff.description or ''}\n\n"
                f"Трафик: {str(tariff.traffic_gb) + ' ГБ' if tariff.traffic_gb else 'безлимит'}\n"
                f"Устройств: {tariff.devices}\n\nВыберите срок:")
        rows = [[(f"{p.days} дн. · {fmt_rub(p.price_kopeks)}", f"u:buy:{tariff_id}:{p.days}")] for p in periods]
    rows.append([("← Тарифы", "u:catalog")])
    await query.message.edit_text(text, reply_markup=kb(rows))
    await query.answer()


@router.callback_query(F.data.startswith("u:buy:"))
async def buy(query: CallbackQuery):
    if not yookassa.ready():
        return await query.answer("Оплата ещё настраивается администратором", show_alert=True)
    _, _, tariff_id, days = query.data.split(":")
    try:
        order = create_order(query.from_user.id, int(tariff_id), int(days))
        order = await start_payment(order.id)
    except ValueError as exc:
        return await query.answer(str(exc), show_alert=True)
    except Exception:
        log.exception("Could not create payment")
        return await query.message.answer("Не удалось создать платёж. Заказ сохранён; напишите в поддержку, если ошибка повторяется.")
    await render_order(query, order)


async def render_order(query: CallbackQuery, order: Order):
    text = (f"💳 Заказ №{order.id}\n"
            f"Стоимость: {fmt_rub(order.price_kopeks)}\n"
            f"Бонусами: {fmt_rub(order.bonus_kopeks)}\n"
            f"К оплате ЮKassa: {fmt_rub(order.cash_kopeks)}\n"
            f"Статус: {order.status}")
    rows = []
    if order.payment_url and order.status == "pending":
        rows.append([InlineKeyboardButton(text="Оплатить через ЮKassa", url=order.payment_url)])
        rows.append([InlineKeyboardButton(text="↻ Проверить оплату", callback_data=f"u:check:{order.id}")])
        rows.append([InlineKeyboardButton(text="Отменить заказ", callback_data=f"u:cancel-order:{order.id}")])
    elif order.status == "paid":
        rows.append([InlineKeyboardButton(text="↻ Завершить активацию", callback_data=f"u:check:{order.id}")])
    rows.append([InlineKeyboardButton(text="← Подписки", callback_data="u:catalog")])
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
            return await query.answer("Заказ не найден", show_alert=True)
    if order.status == "new":
        try:
            order = await start_payment(order.id)
        except Exception:
            log.exception("Could not resume payment")
            return await query.answer("Не удалось создать платёж", show_alert=True)
    await render_order(query, order)


@router.callback_query(F.data.startswith("u:check:"))
async def check_payment(query: CallbackQuery):
    order_id = int(query.data.split(":")[2])
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if not order or order.customer_id != query.from_user.id:
            return await query.answer("Заказ не найден", show_alert=True)
    try:
        order = await (reconcile_payment(order_id) if order.provider_payment_id else start_payment(order_id))
    except Exception:
        log.exception("Payment reconciliation failed")
        return await query.answer("Проверка не удалась, попробуйте позже", show_alert=True)
    await render_order(query, order)


@router.callback_query(F.data.startswith("u:cancel-order:"))
async def cancel_payment_order(query: CallbackQuery):
    order_id = int(query.data.split(":")[2])
    try:
        order = await cancel_order(order_id, query.from_user.id)
    except Exception:
        log.exception("Could not cancel order")
        return await query.answer("Не удалось отменить заказ; проверьте его статус позже", show_alert=True)
    await render_order(query, order)


@router.callback_query(F.data == "u:trial")
async def trial(query: CallbackQuery):
    customer_for(query.from_user.id)
    try:
        customer = await provision_trial(query.from_user.id)
    except ValueError as exc:
        return await query.answer(str(exc), show_alert=True)
    except Exception:
        log.exception("Trial provisioning failed")
        return await query.answer("Пробный доступ пока не удалось выдать. Напишите в поддержку.", show_alert=True)
    await query.message.edit_text(
        "🎁 VPN активирован на 1 день: 20 ГБ и 1 устройство.\n\n"
        f"Ссылка подписки:\n{customer.subscription_url or 'Откройте «Мой ключ» чуть позже'}",
        reply_markup=kb([[ ("🔑 Мой ключ", "u:key") ], [("← Меню", "u:home")]]),
    )
    await query.answer()


@router.callback_query(F.data == "u:key")
async def my_key(query: CallbackQuery):
    customer_for(query.from_user.id)
    with SessionLocal() as db:
        customer = db.get(Customer, query.from_user.id)
    if not customer.subscription_url:
        text = "🔑 У вас пока нет ключа. Выберите тариф или получите пробный день."
    else:
        expiry = aware(customer.expire_at).strftime("%d.%m.%Y %H:%M UTC") if customer.expire_at else "неизвестно"
        text = f"🔑 Ваш ключ\n\nДействует до: {expiry}\n\nСсылка подписки:\n{customer.subscription_url}"
    await query.message.edit_text(text, reply_markup=kb([[ ("📦 Подписка", "u:catalog") ], [("← Меню", "u:home")]]))
    await query.answer()


@router.callback_query(F.data == "u:referrals")
async def referrals(query: CallbackQuery):
    customer_for(query.from_user.id)
    with SessionLocal() as db:
        customer = db.get(Customer, query.from_user.id)
        invited = db.scalar(select(func.count()).select_from(Customer).where(Customer.referred_by == customer.telegram_id)) or 0
    link = f"https://t.me/{settings.bot_username.lstrip('@')}?start=ref_{customer.telegram_id}" if settings.bot_username else "Укажите BOT_USERNAME на сервере"
    await query.message.edit_text(
        "👥 Приглашайте друзей\n\nПосле первой оплаченной подписки друга вам начисляется ⅓ её цены на бонусный баланс. Бонусами можно оплатить продление полностью или частично.\n\n"
        f"Приглашено: {invited}\nБаланс: {fmt_rub(customer.bonus_balance_kopeks)}\n\nВаша ссылка:\n{link}",
        reply_markup=kb([[ ("← Меню", "u:home") ]]),
    )
    await query.answer()


@router.callback_query(F.data == "u:instructions")
async def instructions(query: CallbackQuery):
    text = "📖 Инструкция подключения\n\nПолучите ключ в боте, установите совместимое VPN-приложение и добавьте ссылку подписки. Подробную инструкцию администратор добавит здесь позже."
    if settings.instructions_url:
        text += f"\n\n{settings.instructions_url}"
    await query.message.edit_text(text, reply_markup=kb([[ ("← Меню", "u:home") ]]))
    await query.answer()


@router.callback_query(F.data == "u:reviews")
async def reviews(query: CallbackQuery):
    await query.message.edit_text("⭐ Отзывы\n\n" + (settings.reviews_url or "Раздел отзывов будет открыт позже."), reply_markup=kb([[ ("← Меню", "u:home") ]]))
    await query.answer()


@router.callback_query(F.data == "u:rules")
async def rules(query: CallbackQuery):
    await query.message.edit_text("📄 Правила\n\n" + (settings.rules_url or "Правила сервиса будут опубликованы администратором."), reply_markup=kb([[ ("← Меню", "u:home") ]]))
    await query.answer()


@router.callback_query(F.data == "u:support")
async def support(query: CallbackQuery):
    customer_for(query.from_user.id)
    with SessionLocal() as db:
        tickets = list(db.scalars(select(SupportTicket).where(SupportTicket.customer_id == query.from_user.id).order_by(SupportTicket.id.desc()).limit(5)))
    rows = [[(f"#{t.id} · {t.status}", f"u:ticket:{t.id}")] for t in tickets]
    rows += [[("＋ Новое обращение", "u:ticket-new")], [("← Меню", "u:home")]]
    await query.message.edit_text("💬 Поддержка\n\nОпишите вопрос в тикете — мы ответим здесь, в боте.", reply_markup=kb(rows))
    await query.answer()


@router.callback_query(F.data == "u:ticket-new")
async def ticket_new(query: CallbackQuery, state: FSMContext):
    await state.set_state(TicketForm.new_message)
    await query.message.answer("Напишите сообщение для поддержки одним текстом. /cancel — отмена.")
    await query.answer()


@router.message(TicketForm.new_message)
async def ticket_new_message(message: Message, state: FSMContext):
    content = (message.text or "").strip()
    if not 1 <= len(content) <= 4000:
        return await message.answer("Введите текст длиной до 4000 символов.")
    with SessionLocal.begin() as db:
        ticket = SupportTicket(customer_id=message.from_user.id)
        db.add(ticket)
        db.flush()
        db.add(TicketMessage(ticket_id=ticket.id, sender_id=message.from_user.id, text=content))
        ticket_id = ticket.id
    await state.clear()
    await message.answer(f"✅ Тикет #{ticket_id} создан. Ответ появится здесь, в боте.", reply_markup=main_kb())
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
            return await query.answer("Тикет не найден", show_alert=True)
        messages = list(db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.id.desc()).limit(8)))
    lines = [f"{'Вы' if m.sender_id == query.from_user.id else 'Поддержка'}: {m.text[:500]}" for m in reversed(messages)]
    await query.message.edit_text(f"💬 Тикет #{ticket_id} · {ticket.status}\n\n" + "\n\n".join(lines), reply_markup=kb([[ ("Ответить", f"u:ticket-reply:{ticket_id}") ], [("← Поддержка", "u:support")]]))
    await query.answer()


@router.callback_query(F.data.startswith("u:ticket-reply:"))
async def ticket_reply(query: CallbackQuery, state: FSMContext):
    ticket_id = int(query.data.split(":")[2])
    with SessionLocal() as db:
        ticket = db.get(SupportTicket, ticket_id)
        if not ticket or ticket.customer_id != query.from_user.id:
            return await query.answer("Тикет не найден", show_alert=True)
    await state.update_data(ticket_id=ticket_id)
    await state.set_state(TicketForm.reply_message)
    await query.message.answer(f"Напишите ответ в тикет #{ticket_id}. /cancel — отмена.")
    await query.answer()


@router.message(TicketForm.reply_message)
async def ticket_reply_message(message: Message, state: FSMContext):
    data = await state.get_data()
    content = (message.text or "").strip()
    if not 1 <= len(content) <= 4000:
        return await message.answer("Введите текст длиной до 4000 символов.")
    with SessionLocal.begin() as db:
        ticket = db.get(SupportTicket, data["ticket_id"])
        if not ticket or ticket.customer_id != message.from_user.id:
            return await message.answer("Тикет не найден")
        ticket.status = "open"
        db.add(TicketMessage(ticket_id=ticket.id, sender_id=message.from_user.id, text=content))
    await state.clear()
    await message.answer("Сообщение отправлено в поддержку.", reply_markup=main_kb())
    for admin_id in settings.admins:
        try:
            await message.bot.send_message(admin_id, f"💬 Новый ответ по тикету #{ticket.id}\nОт: {message.from_user.id}\n{content[:1000]}\n\nОткройте /tickets")
        except Exception:
            log.exception("Could not notify admin about ticket reply")
