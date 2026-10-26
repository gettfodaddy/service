from string import Formatter

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import BotMenuButton, BotText, BotUiLabel, SessionLocal
from .settings import settings


# The keys and actions are stable application routes. Admins edit only the
# displayed copy, so a renamed button can never point at a different action.
TEXTS = {
    "brand_name": ("Название сервиса", settings.app_name, set()),
    "welcome": ("Приветствие /start", "🌐 {app_name}\n\n🚀 Как подключиться за 2 минуты:\n\n① Подписка — выберите тариф или пробный день.\n② Мой ключ — получите ссылку подписки.\n③ Инструкция — добавьте ссылку в VPN-приложение.\n④ Нажмите «Подключиться» — готово!\n\n❔ Нужна помощь? Откройте «Инструкция» или «Поддержка».", {"app_name"}),
    "home": ("Главный экран", "🌐 {app_name}\n\nВыберите раздел:", {"app_name"}),
    "catalog": ("Каталог тарифов", "📦 Подписки\n\nВыберите тариф. Цена и условия сохраняются в заказе на момент оформления.", set()),
    "tariff_detail": ("Описание тарифа", "📦 {name}\n{description}\n\nТрафик: {traffic}\nУстройств: {devices}\n\nВыберите срок:", {"name", "description", "traffic", "devices"}),
    "trial_success": ("Пробный доступ выдан", "🎁 VPN активирован на 1 день: 20 ГБ и 1 устройство.\n\nСсылка подписки:\n{subscription_url}", {"subscription_url"}),
    "key_empty": ("Ключа ещё нет", "🔑 У вас пока нет ключа. Выберите тариф или получите пробный день.", set()),
    "key_active": ("Мой ключ", "🔑 Ваш ключ\n\nДействует до: {expiry}\n\nСсылка подписки:\n{subscription_url}", {"expiry", "subscription_url"}),
    "referrals": ("Рефералы", "👥 Приглашайте друзей\n\nПосле первой оплаченной подписки друга вам начисляется ⅓ её цены на бонусный баланс. Бонусами можно оплатить продление полностью или частично.\n\nПриглашено: {invited}\nБаланс: {balance}\n\nВаша ссылка:\n{referral_link}", {"invited", "balance", "referral_link"}),
    "instructions": ("Инструкция", "📖 Инструкция подключения\n\nПолучите ключ в боте, установите совместимое VPN-приложение и добавьте ссылку подписки. Подробную инструкцию администратор добавит здесь позже.\n\n{instructions_url}", {"instructions_url"}),
    "reviews": ("Отзывы", "⭐ Отзывы\n\n{reviews_url}", {"reviews_url"}),
    "rules": ("Правила", "📄 Правила\n\n{rules_url}", {"rules_url"}),
    "support": ("Поддержка", "💬 Поддержка\n\nОпишите вопрос в тикете — мы ответим здесь, в боте.", set()),
    "order": ("Заказ", "💳 Заказ №{order_id}\nСтоимость: {price}\nБонусами: {bonus}\nК оплате ЮKassa: {cash}\nСтатус: {status}", {"order_id", "price", "bonus", "cash", "status"}),
    "return_from_payment": ("Возврат после оплаты", "Проверить оплату можно через «Подписка» → «Мой заказ». Подтверждение от ЮKassa также придёт автоматически.", set()),
    "payment_success": ("Оплата подтверждена", "✅ Оплата подтверждена. Подписка активирована. Откройте «Мой ключ» в боте.", set()),
    "ticket_new_prompt": ("Вопрос для поддержки", "Напишите сообщение для поддержки одним текстом. /cancel — отмена.", set()),
    "ticket_created": ("Обращение создано", "✅ Тикет #{ticket_id} создан. Ответ появится здесь, в боте.", {"ticket_id"}),
    "ticket_reply_prompt": ("Ответ в обращении", "Напишите ответ в тикет #{ticket_id}. /cancel — отмена.", {"ticket_id"}),
    "ticket_reply_sent": ("Ответ отправлен", "Сообщение отправлено в поддержку.", set()),
    "action_canceled": ("Действие отменено", "Действие отменено.", set()),
    "tariff_unavailable": ("Тариф недоступен", "Тариф недоступен", set()),
    "payment_unconfigured": ("Оплата не настроена", "Оплата ещё настраивается администратором", set()),
    "payment_create_failed": ("Ошибка создания платежа", "Не удалось создать платёж. Заказ сохранён; напишите в поддержку, если ошибка повторяется.", set()),
    "order_not_found": ("Заказ не найден", "Заказ не найден", set()),
    "payment_resume_failed": ("Ошибка повторной оплаты", "Не удалось создать платёж", set()),
    "payment_check_failed": ("Ошибка проверки оплаты", "Проверка не удалась, попробуйте позже", set()),
    "payment_cancel_failed": ("Ошибка отмены заказа", "Не удалось отменить заказ; проверьте его статус позже", set()),
    "trial_failed": ("Ошибка пробного доступа", "Пробный доступ пока не удалось выдать. Напишите в поддержку.", set()),
    "ticket_invalid_length": ("Неверная длина сообщения", "Введите текст длиной до 4000 символов.", set()),
    "ticket_not_found": ("Обращение не найдено", "Тикет не найден", set()),
}

ALERT_TEXTS = {"tariff_unavailable", "payment_unconfigured", "order_not_found",
               "payment_resume_failed", "payment_check_failed", "payment_cancel_failed",
               "trial_failed", "ticket_not_found"}
SYSTEM_TEXTS = ALERT_TEXTS | {"action_canceled", "payment_create_failed", "ticket_invalid_length"}

MENU_BUTTONS = (
    ("catalog", "▣ Подписка"),
    ("key", "🗝 Мой ключ"),
    ("trial", "🆓 Бесплатный ключ"),
    ("referrals", "♧ Рефералы"),
    ("instructions", "📖 Инструкция"),
    ("support", "☏ Поддержка"),
    ("reviews", "☆ Отзывы ↗"),
    ("rules", "▤ Правила"),
)

UI_LABELS = {
    "back_home": ("Назад в меню", "← Меню", set()),
    "open_catalog": ("Открыть подписки", "📦 Подписка", set()),
    "open_key": ("Открыть ключ", "🔑 Мой ключ", set()),
    "back_catalog": ("Назад к тарифам", "← Тарифы", set()),
    "back_support": ("Назад к поддержке", "← Поддержка", set()),
    "my_order": ("Мой заказ", "💳 Мой заказ №{order_id}", {"order_id"}),
    "tariff": ("Карточка тарифа", "📦 {name}", {"name"}),
    "tariff_period": ("Период тарифа", "{days} дн. · {price}", {"days", "price"}),
    "pay": ("Оплатить", "Оплатить через ЮKassa", set()),
    "check_payment": ("Проверить оплату", "↻ Проверить оплату", set()),
    "cancel_order": ("Отменить заказ", "Отменить заказ", set()),
    "finish_activation": ("Завершить активацию", "↻ Завершить активацию", set()),
    "new_ticket": ("Новое обращение", "＋ Новое обращение", set()),
    "ticket": ("Обращение", "#{ticket_id} · {status}", {"ticket_id", "status"}),
    "reply_ticket": ("Ответить в обращении", "Ответить", set()),
}


def ensure_bot_content(db: Session) -> None:
    changed = False
    for key, (_, default, _) in TEXTS.items():
        if db.get(BotText, key) is None:
            db.add(BotText(key=key, body=default))
            changed = True
    for position, (action, label) in enumerate(MENU_BUTTONS):
        if db.get(BotMenuButton, action) is None:
            db.add(BotMenuButton(action=action, label=label, position=position, visible=True))
            changed = True
    for key, (_, label, _) in UI_LABELS.items():
        if db.get(BotUiLabel, key) is None:
            db.add(BotUiLabel(key=key, label=label))
            changed = True
    if changed:
        db.commit()


def validate_text(key: str, body: str) -> None:
    if key not in TEXTS:
        raise ValueError("Неизвестный текст бота")
    limit = 180 if key in ALERT_TEXTS else 3000
    if not body.strip() or len(body) > limit:
        raise ValueError(f"Текст должен содержать от 1 до {limit} символов")
    if key == "brand_name" and (len(body.strip()) > 60 or "\n" in body):
        raise ValueError("Название сервиса: одна строка, максимум 60 символов")
    allowed = TEXTS[key][2]
    try:
        for _, field, spec, conversion in Formatter().parse(body):
            if field is None:
                continue
            if field not in allowed or spec or conversion:
                raise ValueError(f"Допустимые переменные: {', '.join(sorted(allowed)) or 'нет'}")
    except (ValueError, KeyError) as exc:
        raise ValueError(f"Проверьте фигурные скобки и переменные: {exc}") from exc


def get_text(key: str, **values: object) -> str:
    with SessionLocal() as db:
        row = db.get(BotText, key)
        brand_row = db.get(BotText, "brand_name")
    template = row.body if row else TEXTS[key][1]
    context = {"app_name": brand_row.body if brand_row else settings.app_name, "instructions_url": settings.instructions_url,
               "reviews_url": settings.reviews_url or "Раздел отзывов будет открыт позже.",
               "rules_url": settings.rules_url or "Правила сервиса будут опубликованы администратором."}
    context.update(values)
    return template.format_map(context).strip()


def get_menu_buttons() -> list[BotMenuButton]:
    with SessionLocal() as db:
        return list(db.scalars(select(BotMenuButton).where(BotMenuButton.visible.is_(True)).order_by(BotMenuButton.position, BotMenuButton.action)))


def validate_label(key: str, label: str) -> None:
    if key not in UI_LABELS or not label.strip() or len(label) > 64:
        raise ValueError("Подпись кнопки должна содержать от 1 до 64 символов")
    allowed = UI_LABELS[key][2]
    try:
        for _, field, spec, conversion in Formatter().parse(label):
            if field is not None and (field not in allowed or spec or conversion):
                raise ValueError(f"Допустимые переменные: {', '.join(sorted(allowed)) or 'нет'}")
    except ValueError as exc:
        raise ValueError(f"Проверьте подпись кнопки: {exc}") from exc


def ui_label(key: str, **values: object) -> str:
    with SessionLocal() as db:
        row = db.get(BotUiLabel, key)
    return (row.label if row else UI_LABELS[key][1]).format_map(values)
