from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from .db import AuditEvent, BonusEntry, Customer, Order, SessionLocal, Tariff, now_utc
from .remnawave import create_user, get_user_by_username, reset_user_traffic, update_user
from . import yookassa


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def customer_for(telegram_id: int, referrer_id: int | None = None) -> Customer:
    with SessionLocal.begin() as db:
        row = db.get(Customer, telegram_id)
        if row is None:
            parent = db.get(Customer, referrer_id) if referrer_id and referrer_id != telegram_id else None
            row = Customer(telegram_id=telegram_id, referred_by=parent.telegram_id if parent else None)
            db.add(row)
            db.flush()
        return row


def create_order(telegram_id: int, tariff_id: int, days: int) -> Order:
    with SessionLocal.begin() as db:
        customer = db.scalar(select(Customer).where(Customer.telegram_id == telegram_id).with_for_update())
        if customer is None:
            raise ValueError("Сначала откройте /start")
        pending = db.scalar(select(Order).where(Order.customer_id == telegram_id, Order.status.in_(["new", "pending", "paid"])).order_by(Order.id.desc()).limit(1))
        if pending:
            raise ValueError(f"Сначала завершите заказ №{pending.id}")
        tariff = db.get(Tariff, tariff_id)
        if not tariff or not tariff.published or tariff.is_trial or not tariff.squads:
            raise ValueError("Тариф сейчас недоступен")
        period = next((p for p in tariff.periods if p.days == days), None)
        if period is None or period.price_kopeks < 100:
            raise ValueError("Период сейчас недоступен")
        available = max(0, customer.bonus_balance_kopeks - customer.bonus_reserved_kopeks)
        bonus = min(available, period.price_kopeks) if customer.first_paid_order_id else 0
        # Most YooKassa methods require at least 1 RUB. If the balance cannot
        # cover the entire price, leave at least that much for the provider.
        if 0 < period.price_kopeks - bonus < 100:
            bonus = max(0, period.price_kopeks - 100)
        customer.bonus_reserved_kopeks += bonus
        order = Order(
            customer_id=telegram_id, tariff_id=tariff_id, status="new", days=days,
            price_kopeks=period.price_kopeks, bonus_kopeks=bonus,
            cash_kopeks=period.price_kopeks - bonus, traffic_gb=tariff.traffic_gb,
            devices=tariff.devices, squads_csv=",".join(s.squad_uuid for s in tariff.squads),
        )
        db.add(order)
        db.flush()
        db.add(AuditEvent(admin_id=0, action="order.created", entity_id=order.id,
                          detail=f"Клиент {telegram_id} · тариф {tariff.name} · {days} дн. · {order.price_kopeks / 100:.2f} ₽"))
        return order


async def start_payment(order_id: int) -> Order:
    with SessionLocal.begin() as db:
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if not order:
            raise ValueError("Заказ не найден")
        if order.status == "complete" or order.provider_payment_id:
            return order
        if order.status == "paid":
            needs_fulfillment = True
        elif order.status != "new":
            raise ValueError("Этот заказ больше нельзя оплатить")
        elif order.cash_kopeks == 0:
            order.status = "paid"
            needs_fulfillment = True
        else:
            # Serialize creation and cancellation of this order. YooKassa's
            # idempotence key also makes a retry reuse the same payment.
            payment = await yookassa.create_payment(order_id, order.cash_kopeks)
            payment_url = payment.get("confirmation", {}).get("confirmation_url")
            if not payment.get("id") or not payment_url:
                raise RuntimeError("ЮKassa не вернула ссылку на оплату")
            order.provider_payment_id = payment["id"]
            order.payment_url = payment_url
            order.status = "pending"
            db.add(AuditEvent(admin_id=0, action="payment.created", entity_id=order.id,
                              detail=f"Клиент {order.customer_id} · {order.cash_kopeks / 100:.2f} ₽"))
            needs_fulfillment = False
    if needs_fulfillment:
        return await fulfill_order(order_id)
    return order


async def provision_trial(telegram_id: int) -> Customer:
    with SessionLocal.begin() as db:
        customer = db.scalar(select(Customer).where(Customer.telegram_id == telegram_id).with_for_update())
        if not customer or customer.trial_used or customer.remna_username:
            raise ValueError("Пробный доступ уже использован или у вас есть подписка")
        trial = db.scalar(select(Tariff).where(Tariff.is_trial.is_(True), Tariff.published.is_(True)))
        if not trial or not trial.squads:
            raise ValueError("Пробный тариф пока не настроен администратором")
        squads = [s.squad_uuid for s in trial.squads]
        username = f"cv{telegram_id}"
        existing = await get_user_by_username(username)
        if existing:
            if existing.get("telegramId") != telegram_id:
                raise RuntimeError("Имя пользователя Remnawave занято")
            remote = existing
        else:
            remote = await create_user(username=username, telegram_id=telegram_id, expire_at=now_utc() + timedelta(days=1), traffic_gb=20, devices=1, squads=squads)
        if not remote.get("subscriptionUrl"):
            remote = await get_user_by_username(username) or remote
        customer.remna_username = username
        customer.subscription_url = remote.get("subscriptionUrl")
        customer.expire_at = parse_date(remote.get("expireAt")) or now_utc() + timedelta(days=1)
        customer.trial_used = True
        db.add(AuditEvent(admin_id=0, action="trial.activated", detail=f"Клиент {telegram_id} · 1 день · 20 ГБ"))
        return customer


async def fulfill_order(order_id: int) -> Order:
    # Commit the absolute target before a remote side effect. A retry can then
    # apply that same date and cannot extend a subscription twice.
    with SessionLocal.begin() as db:
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if not order:
            raise ValueError("Заказ не найден")
        if order.status in {"complete", "canceled"}:
            return order
        if order.status != "paid":
            raise ValueError("Заказ ещё не оплачен")
        if order.target_expire_at is None:
            customer = db.get(Customer, order.customer_id)
            username = customer.remna_username or f"cv{customer.telegram_id}"
            remote = await get_user_by_username(username)
            if remote and remote.get("telegramId") != customer.telegram_id:
                raise RuntimeError("Пользователь Remnawave принадлежит другому Telegram ID")
            remote_expire = parse_date(remote.get("expireAt")) if remote else None
            order.target_expire_at = max(now_utc(), remote_expire or now_utc()) + timedelta(days=order.days)
    # Lock through the remote operation; duplicate callbacks cannot credit twice.
    with SessionLocal.begin() as db:
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if not order:
            raise ValueError("Заказ не найден")
        if order.status == "complete":
            return order
        if order.status != "paid":
            raise ValueError("Заказ ещё не оплачен")
        customer = db.scalar(select(Customer).where(Customer.telegram_id == order.customer_id).with_for_update())
        username = customer.remna_username or f"cv{customer.telegram_id}"
        remote = await get_user_by_username(username)
        if remote and remote.get("telegramId") != customer.telegram_id:
            raise RuntimeError("Пользователь Remnawave принадлежит другому Telegram ID")
        squads = order.squads_csv.split(",")
        if remote:
            remote_expire = parse_date(remote.get("expireAt"))
            effective_expire = max(aware(order.target_expire_at), remote_expire or aware(order.target_expire_at))
            remote = await update_user(username=username, expire_at=effective_expire, traffic_gb=order.traffic_gb, devices=order.devices, squads=squads)
            latest = await get_user_by_username(username) or remote
            last_reset = parse_date(latest.get("lastTrafficResetAt"))
            if not last_reset or last_reset < aware(order.created_at):
                if latest.get("id") is None:
                    raise RuntimeError("Remnawave did not return numeric user ID")
                await reset_user_traffic(int(latest["id"]))
            remote = await get_user_by_username(username) or latest
        else:
            remote = await create_user(username=username, telegram_id=customer.telegram_id, expire_at=order.target_expire_at, traffic_gb=order.traffic_gb, devices=order.devices, squads=squads)
            effective_expire = aware(order.target_expire_at)
        if not remote.get("subscriptionUrl"):
            remote = await get_user_by_username(username) or remote
        customer.remna_username = username
        customer.subscription_url = remote.get("subscriptionUrl") or customer.subscription_url
        customer.expire_at = effective_expire
        if order.bonus_kopeks:
            customer.bonus_reserved_kopeks -= order.bonus_kopeks
            customer.bonus_balance_kopeks -= order.bonus_kopeks
            db.add(BonusEntry(customer_id=customer.telegram_id, order_id=order.id, kind="spend", amount_kopeks=-order.bonus_kopeks))
        if customer.first_paid_order_id is None:
            customer.first_paid_order_id = order.id
            if customer.referred_by and customer.referred_by != customer.telegram_id:
                inviter = db.scalar(select(Customer).where(Customer.telegram_id == customer.referred_by).with_for_update())
                if inviter:
                    reward = order.price_kopeks // 3
                    inviter.bonus_balance_kopeks += reward
                    db.add(BonusEntry(customer_id=inviter.telegram_id, order_id=order.id, kind="referral", amount_kopeks=reward))
                    db.add(AuditEvent(admin_id=0, action="order.referral_credited", entity_id=order.id,
                                      detail=f"Пригласивший {inviter.telegram_id} · {reward / 100:.2f} ₽"))
        order.status = "complete"
        order.completed_at = now_utc()
        db.add(AuditEvent(admin_id=0, action="order.completed", entity_id=order.id,
                          detail=f"Клиент {order.customer_id} · подписка активирована"))
        return order


async def reconcile_payment(order_id: int) -> Order:
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if not order or not order.provider_payment_id:
            raise ValueError("Платёж заказа не найден")
        payment_id = order.provider_payment_id
    payment = await yookassa.get_payment(payment_id)
    with SessionLocal.begin() as db:
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order.status in {"complete", "canceled"}:
            return order
        if payment.get("id") != order.provider_payment_id or str(payment.get("metadata", {}).get("order_id")) != str(order.id):
            raise RuntimeError("Платёж не соответствует заказу")
        amount = payment.get("amount", {})
        if amount.get("currency") != "RUB" or Decimal(str(amount.get("value", "0"))) * 100 != order.cash_kopeks:
            raise RuntimeError("Сумма платежа не соответствует заказу")
        if payment.get("status") == "canceled":
            order.status = "canceled"
            db.add(AuditEvent(admin_id=0, action="payment.canceled", entity_id=order.id,
                              detail=f"Клиент {order.customer_id}"))
            customer = db.scalar(select(Customer).where(Customer.telegram_id == order.customer_id).with_for_update())
            customer.bonus_reserved_kopeks -= order.bonus_kopeks
            return order
        if payment.get("status") != "succeeded":
            return order
        order.status = "paid"
        db.add(AuditEvent(admin_id=0, action="payment.confirmed", entity_id=order.id,
                          detail=f"Клиент {order.customer_id} · {order.cash_kopeks / 100:.2f} ₽"))
    return await fulfill_order(order_id)


async def cancel_order(order_id: int, customer_id: int) -> Order:
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if not order or order.customer_id != customer_id:
            raise ValueError("Заказ не найден")
        if order.status in {"complete", "canceled"}:
            return order
        payment_id = order.provider_payment_id
    if payment_id:
        try:
            await yookassa.cancel_payment(payment_id, order_id)
        except Exception:
            # It may have just succeeded. Read the authoritative status.
            return await reconcile_payment(order_id)
        return await reconcile_payment(order_id)
    with SessionLocal.begin() as db:
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order.status == "new":
            customer = db.scalar(select(Customer).where(Customer.telegram_id == customer_id).with_for_update())
            customer.bonus_reserved_kopeks -= order.bonus_kopeks
            order.status = "canceled"
            db.add(AuditEvent(admin_id=0, action="order.canceled", entity_id=order.id,
                              detail=f"Клиент {customer_id}"))
        return order
