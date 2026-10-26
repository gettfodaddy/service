from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import httpx

from .settings import settings


def ready() -> bool:
    return bool(settings.yookassa_shop_id and settings.yookassa_secret_key and settings.bot_username)


def amount(kopeks: int) -> str:
    return f"{Decimal(kopeks) / 100:.2f}"


def _client() -> httpx.AsyncClient:
    if not ready():
        raise RuntimeError("YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY and BOT_USERNAME are required")
    return httpx.AsyncClient(
        base_url="https://api.yookassa.ru/v3",
        auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
        timeout=25,
    )


async def create_payment(order_id: int, kopeks: int) -> dict:
    payload = {
        "amount": {"value": amount(kopeks), "currency": "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/{settings.bot_username.lstrip('@')}?start=order_{order_id}",
        },
        "description": f"VPN подписка, заказ {order_id}"[:128],
        "metadata": {"order_id": str(order_id)},
    }
    key = str(uuid5(NAMESPACE_URL, f"vpn-bot-payment-{order_id}"))
    async with _client() as client:
        response = await client.post("/payments", json=payload, headers={"Idempotence-Key": key})
        response.raise_for_status()
        return response.json()


async def get_payment(payment_id: str) -> dict:
    async with _client() as client:
        response = await client.get(f"/payments/{payment_id}")
        response.raise_for_status()
        return response.json()


async def cancel_payment(payment_id: str, order_id: int) -> dict:
    key = str(uuid5(NAMESPACE_URL, f"vpn-bot-cancel-{order_id}"))
    async with _client() as client:
        response = await client.post(f"/payments/{payment_id}/cancel", headers={"Idempotence-Key": key})
        response.raise_for_status()
        return response.json()
