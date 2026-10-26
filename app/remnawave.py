import httpx
from datetime import datetime, timezone

from .settings import settings


def _headers() -> dict[str, str]:
    if not settings.remnawave_api_url or not settings.remnawave_api_key:
        raise RuntimeError("Remnawave API URL/key are not configured")
    return {"Authorization": f"Bearer {settings.remnawave_api_key}", "X-Api-Key": settings.remnawave_api_key}


def _body(response: httpx.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
        return payload["response"]
    if isinstance(payload, dict):
        return payload
    raise ValueError("Unexpected Remnawave response")


def _user(payload: dict) -> dict:
    return payload.get("user", payload)


async def get_user_by_username(username: str) -> dict | None:
    async with httpx.AsyncClient(base_url=settings.remnawave_api_url.rstrip("/"), headers=_headers(), timeout=20) as client:
        response = await client.get(f"/api/users/by-username/{username}")
    if response.status_code == 404:
        return None
    return _user(_body(response))


async def create_user(*, username: str, telegram_id: int, expire_at: datetime, traffic_gb: int, devices: int, squads: list[str]) -> dict:
    payload = {
        "username": username,
        "expireAt": expire_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "ACTIVE",
        "trafficLimitBytes": traffic_gb * 1024**3 if traffic_gb else 0,
        "trafficLimitStrategy": "NO_RESET",
        "telegramId": telegram_id,
        "hwidDeviceLimit": devices,
        "activeInternalSquads": squads,
        "description": "Created by VPN bot",
    }
    async with httpx.AsyncClient(base_url=settings.remnawave_api_url.rstrip("/"), headers=_headers(), timeout=25) as client:
        response = await client.post("/api/users", json=payload)
    return _user(_body(response))


async def update_user(*, username: str, expire_at: datetime, traffic_gb: int, devices: int, squads: list[str]) -> dict:
    payload = {
        "username": username,
        "expireAt": expire_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "ACTIVE",
        "trafficLimitBytes": traffic_gb * 1024**3 if traffic_gb else 0,
        "trafficLimitStrategy": "NO_RESET",
        "hwidDeviceLimit": devices,
        "activeInternalSquads": squads,
    }
    async with httpx.AsyncClient(base_url=settings.remnawave_api_url.rstrip("/"), headers=_headers(), timeout=25) as client:
        response = await client.patch("/api/users", json=payload)
    return _user(_body(response))


async def reset_user_traffic(user_id: int) -> dict:
    async with httpx.AsyncClient(base_url=settings.remnawave_api_url.rstrip("/"), headers=_headers(), timeout=25) as client:
        response = await client.post(f"/api/users/{user_id}/actions/reset-traffic")
    response.raise_for_status()
    return _user(_body(response)) if response.content else {}


async def fetch_internal_squads() -> list[dict[str, str]]:
    async with httpx.AsyncClient(base_url=settings.remnawave_api_url.rstrip("/"), headers=_headers(), timeout=15) as client:
        response = await client.get("/api/internal-squads")
        response.raise_for_status()
        data = response.json()
    payload = data.get("response", data) if isinstance(data, dict) else data
    if isinstance(payload, dict):
        payload = payload.get("internalSquads", payload.get("squads", payload.get("items", [])))
    if not isinstance(payload, list):
        raise ValueError("Unexpected Remnawave squads response")
    return [{"uuid": str(item["uuid"]), "name": str(item.get("name") or item["uuid"])} for item in payload]
