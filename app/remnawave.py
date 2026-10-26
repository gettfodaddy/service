import httpx

from .settings import settings


async def fetch_internal_squads() -> list[dict[str, str]]:
    if not settings.remnawave_api_url or not settings.remnawave_api_key:
        raise RuntimeError("Remnawave API URL/key are not configured")
    headers = {
        "Authorization": f"Bearer {settings.remnawave_api_key}",
        "X-Api-Key": settings.remnawave_api_key,
    }
    async with httpx.AsyncClient(base_url=settings.remnawave_api_url.rstrip("/"), timeout=15) as client:
        response = await client.get("/api/internal-squads", headers=headers)
        response.raise_for_status()
        data = response.json()
    payload = data.get("response", data) if isinstance(data, dict) else data
    if isinstance(payload, dict):
        payload = payload.get("internalSquads", payload.get("squads", payload.get("items", [])))
    if not isinstance(payload, list):
        raise ValueError("Unexpected Remnawave squads response")
    return [{"uuid": str(item["uuid"]), "name": str(item.get("name") or item["uuid"])} for item in payload]
