"""Post embeds through Discord's REST API (no gateway connection needed).

Used by ``python -m gold_bot --send-now`` to post a one-off test report.
"""
from __future__ import annotations

import discord
import httpx

API = "https://discord.com/api/v10"


class RestPublisher:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=API, timeout=30, transport=transport, headers={"Authorization": f"Bot {token}"}
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, channel_id: int, embed: discord.Embed) -> str:
        response = await self._client.post(f"/channels/{channel_id}/messages", json={"embeds": [embed.to_dict()]})
        if response.status_code >= 300:
            raise RuntimeError(f"Discord HTTP {response.status_code}: {response.text[:300]}")
        return response.json()["id"]
