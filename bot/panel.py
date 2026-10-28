"""Клиент API панели 3x-ui (v3). Авторизация по API-токену: Authorization: Bearer <токен>."""
import json

import aiohttp


class PanelError(Exception):
    pass


class Panel:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/") + "/panel/api"
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._session: aiohttp.ClientSession | None = None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.headers, timeout=aiohttp.ClientTimeout(total=20)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _call(self, method: str, path: str, payload=None):
        http = await self._http()
        async with http.request(method, self.base + path, json=payload) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise PanelError(f"{method} {path}: HTTP {resp.status} {text[:200]}")
        try:
            data = json.loads(text)
        except ValueError:
            raise PanelError(f"{method} {path}: не JSON: {text[:200]}")
        if not data.get("success"):
            raise PanelError(f"{method} {path}: {data.get('msg') or 'ошибка панели'}")
        return data.get("obj")

    # --- подключения (inbounds) ---
    async def list_inbounds(self) -> list[dict]:
        return await self._call("GET", "/inbounds/list") or []

    async def add_inbound(self, inbound: dict) -> dict:
        return await self._call("POST", "/inbounds/add", inbound)

    # --- клиенты ---
    async def add_client(self, inbound_id: int, client: dict):
        await self._call("POST", "/clients/add", {"client": client, "inboundIds": [inbound_id]})

    async def update_client(self, email: str, client: dict):
        await self._call("POST", f"/clients/update/{email}", client)

    async def delete_client(self, email: str):
        await self._call("POST", f"/clients/del/{email}")

    async def client_traffic(self, email: str) -> dict:
        return await self._call("GET", f"/clients/traffic/{email}") or {}

    async def onlines(self) -> list[str]:
        return await self._call("POST", "/clients/onlines") or []

    # --- сервер ---
    async def status(self) -> dict:
        return await self._call("GET", "/server/status") or {}
