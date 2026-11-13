"""OANDA v20 REST market-data provider (read-only).

Uses only these documented v20 endpoints:
  GET /v3/accounts                                            find the account id
  GET /v3/accounts/{id}/instruments?instruments=XAU_USD       display precision
  GET /v3/accounts/{id}/instruments/{instrument}/candles      OHLC history
  GET /v3/accounts/{id}/pricing?instruments=XAU_USD           live bid/ask

Candles are requested with price=B (bid), dailyAlignment=17 and
alignmentTimezone=America/New_York, so daily candles run 17:00 -> 17:00 New York
and 4H candles are aligned to that daily open - the same boundaries as FXCM.
Nothing here can place or change orders.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from gold_bot.data.provider import DataUnavailable, normalize_ohlc
from gold_bot.models import Candle, Price, Timeframe

log = logging.getLogger(__name__)

HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}
GRANULARITY = {
    Timeframe.M5: "M5",
    Timeframe.M15: "M15",
    Timeframe.H1: "H1",
    Timeframe.H4: "H4",
    Timeframe.D1: "D",
}
MAX_COUNT = 5000


def parse_time(value: str | None) -> datetime | None:
    """Parse OANDA RFC3339 times (nanosecond precision) to aware UTC."""
    if not value:
        return None
    text = value.rstrip("Z")
    if "." in text:
        head, frac = text.split(".", 1)
        text = f"{head}.{frac[:6]}"
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


class OandaProvider:
    name = "OANDA"

    def __init__(
        self,
        token: str,
        account_id: str = "",
        environment: str = "practice",
        instrument: str = "XAU_USD",
        smooth: bool = False,
        timeout: float = 30.0,
        retries: int = 3,
        retry_delay: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if environment not in HOSTS:
            raise ValueError("OANDA_ENVIRONMENT must be 'practice' or 'live'")
        self.instrument = instrument
        self.environment = environment
        self._account_id = account_id or None
        self._smooth = smooth
        self._digits: int | None = None
        self._retries = max(1, retries)
        self._retry_delay = retry_delay
        self._last_ok: datetime | None = None
        self._last_error: str | None = None
        self._client = httpx.AsyncClient(
            base_url=HOSTS[environment],
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {token}", "Accept-Datetime-Format": "RFC3339"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    async def _get(self, path: str, params: dict | None = None) -> dict:
        last_error = "unknown error"
        for attempt in range(1, self._retries + 1):
            try:
                response = await self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = f"OANDA unreachable: {exc!r}"
            else:
                if response.status_code == 200:
                    self._last_ok, self._last_error = datetime.now(timezone.utc), None
                    return response.json()
                try:
                    detail = response.json().get("errorMessage", response.text)
                except ValueError:
                    detail = response.text
                last_error = f"OANDA HTTP {response.status_code}: {detail}"
                if response.status_code in (400, 401, 403, 404, 405):
                    break  # not transient: bad request, bad token, wrong account
            if attempt < self._retries:
                await asyncio.sleep(self._retry_delay * 2 ** (attempt - 1))
        self._last_error = last_error
        log.warning("OANDA data unavailable for %s: %s", path, last_error)
        raise DataUnavailable(last_error)

    async def account_id(self) -> str:
        if self._account_id is None:
            accounts = (await self._get("/v3/accounts")).get("accounts", [])
            if not accounts:
                raise DataUnavailable("this OANDA token has no accounts")
            self._account_id = accounts[0]["id"]
            log.info("Using OANDA account %s (%s)", self._account_id, self.environment)
        return self._account_id

    async def digits(self) -> int:
        if self._digits is None:
            acct = await self.account_id()
            data = await self._get(f"/v3/accounts/{acct}/instruments", {"instruments": self.instrument})
            instruments = data.get("instruments", [])
            if not instruments:
                raise DataUnavailable(f"{self.instrument} is not available on this OANDA account")
            self._digits = int(instruments[0]["displayPrecision"])
        return self._digits

    # ------------------------------------------------------------------ #
    async def get_candles(
        self,
        timeframe: Timeframe,
        count: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        acct = await self.account_id()
        params: dict[str, str | int] = {
            "price": "B",
            "granularity": GRANULARITY[timeframe],
            "dailyAlignment": 17,
            "alignmentTimezone": "America/New_York",
            "smooth": "true" if self._smooth else "false",
        }
        if start is not None:
            params["from"] = _iso(start)
            if end is not None:
                params["to"] = _iso(min(end, datetime.now(timezone.utc)))
            else:
                params["count"] = min(count or MAX_COUNT, MAX_COUNT)
        else:
            params["count"] = min(count or 500, MAX_COUNT)
            if end is not None:
                params["to"] = _iso(end)
        data = await self._get(f"/v3/accounts/{acct}/instruments/{self.instrument}/candles", params)
        candles = []
        for raw in data.get("candles", []):
            bid = raw.get("bid")
            if not bid:
                continue
            candle = Candle(
                time=parse_time(raw["time"]),
                open=float(bid["o"]),
                high=float(bid["h"]),
                low=float(bid["l"]),
                close=float(bid["c"]),
                volume=int(raw.get("volume", 0)),
            )
            candles.append(normalize_ohlc(candle, "OANDA"))
        candles.sort(key=lambda c: c.time)
        return candles

    async def get_price(self) -> Price:
        acct = await self.account_id()
        digits = await self.digits()
        data = await self._get(f"/v3/accounts/{acct}/pricing", {"instruments": self.instrument})
        prices = data.get("prices", [])
        if not prices or not prices[0].get("bids") or not prices[0].get("asks"):
            raise DataUnavailable(f"no live quote for {self.instrument}")
        p = prices[0]
        return Price(
            instrument=self.instrument.replace("_", "/"),
            bid=float(p["bids"][0]["price"]),
            ask=float(p["asks"][0]["price"]),
            time=parse_time(p.get("time")),
            digits=digits,
            point_size=10 ** -digits,
        )

    async def health(self) -> dict:
        try:
            price = await self.get_price()
            connected, status = True, "CONNECTED"
        except DataUnavailable as exc:
            price, connected, status = None, False, "ERROR"
            self._last_error = str(exc)
        return {
            "connected": connected,
            "status": status,
            "provider": self.name,
            "connection": self.environment,
            "instrument": self.instrument,
            "last_price_received_at": price.time.isoformat() if price and price.time else None,
            "reconnects": 0,
            "last_error": self._last_error,
        }
