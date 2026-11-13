"""HTTP client for the FXCM ForexConnect bridge (see fxcm_bridge/)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from gold_bot.data.provider import DataUnavailable, normalize_ohlc
from gold_bot.models import Candle, Price, Timeframe

log = logging.getLogger(__name__)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BridgeClient:
    name = "FXCM"

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay: float = 1.0,
    ) -> None:
        # trust_env=False: the bridge is an internal service, never via a proxy.
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport, trust_env=False)
        self._retries = max(1, retries)
        self._retry_delay = retry_delay

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        last_error: str = "unknown error"
        for attempt in range(1, self._retries + 1):
            try:
                response = await self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = f"bridge unreachable: {exc!r}"
            else:
                if response.status_code == 200:
                    return response.json()
                try:
                    detail = response.json().get("error", response.text)
                except ValueError:
                    detail = response.text
                last_error = f"bridge HTTP {response.status_code}: {detail}"
                if response.status_code in (400, 404):
                    break  # not transient
            if attempt < self._retries:
                await asyncio.sleep(self._retry_delay * 2 ** (attempt - 1))
        log.warning("FXCM data unavailable for %s %s: %s", path, params or "", last_error)
        raise DataUnavailable(last_error)

    async def get_candles(
        self,
        timeframe: Timeframe,
        count: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        params: dict[str, str | int] = {"timeframe": timeframe.value}
        if count is not None:
            params["count"] = count
        if start is not None:
            params["from"] = _iso(start)
        if end is not None:
            params["to"] = _iso(end)
        payload = await self._get("/candles", params)
        candles = [
            Candle(
                time=_parse_time(c["time"]),
                open=float(c["open"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"]),
                volume=int(c.get("volume", 0)),
            )
            for c in payload.get("candles", [])
        ]
        candles.sort(key=lambda c: c.time)
        return [normalize_ohlc(c, "FXCM") for c in candles]

    async def get_price(self) -> Price:
        p = await self._get("/price")
        return Price(
            instrument=p["instrument"],
            bid=float(p["bid"]),
            ask=float(p["ask"]),
            time=_parse_time(p.get("time")),
            digits=int(p.get("digits", 2)),
            point_size=float(p.get("point_size", 0.01)),
        )

    async def health(self) -> dict:
        try:
            response = await self._client.get("/health")
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"connected": False, "status": "UNREACHABLE", "last_error": repr(exc)}
