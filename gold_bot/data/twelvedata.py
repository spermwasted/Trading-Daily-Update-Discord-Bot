"""Twelve Data market-data provider (read-only) with a credit-saving cache.

Endpoint used: GET https://api.twelvedata.com/time_series
(symbol=XAU/USD, interval=5min|1h, outputsize<=5000, timezone=UTC). The API key
is sent in the ``Authorization: apikey <key>`` header, never in URLs or logs.

The free plan allows 800 API credits per day and 8 per minute (one time_series
call = one credit), so the provider:
  * downloads 5000 1h + 5000 5min candles once at start-up,
  * then fetches only the newest 5min candles every ``refresh_seconds``
    (default 150 s), or every ``fast_refresh_seconds`` (20 s) for a few minutes
    after each session open and the daily open, and every 30 min at weekends,
  * builds m15 from m5, and H4 / D1 from 1h, on FXCM's 17:00 New York
    boundaries (see gold_bot/data/aggregate.py).
Typical use is about 650 credits on a trading day.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

from gold_bot.data.aggregate import aggregate, bucket_start
from gold_bot.data.provider import DataUnavailable, normalize_ohlc
from gold_bot.models import Candle, Price, Timeframe
from gold_bot.timeutils import NEW_YORK, market_closed

log = logging.getLogger(__name__)
UTC = timezone.utc
BASE_URL = "https://api.twelvedata.com"
MAX_OUTPUTSIZE = 5000


def utc_now() -> datetime:
    return datetime.now(UTC)


def price_decimals(values: list[dict]) -> int:
    """Quote precision, read from the price strings themselves (e.g. "4092.41" -> 2)."""
    best = 0
    for v in values:
        for key in ("open", "high", "low", "close"):
            text = str(v.get(key, ""))
            if "." in text:
                best = max(best, len(text.split(".", 1)[1].rstrip("0")))
    return min(best, 6)


def parse_values(values: list[dict]) -> list[Candle]:
    candles = []
    for v in values:
        moment = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        candle = Candle(
            time=moment,
            open=float(v["open"]),
            high=float(v["high"]),
            low=float(v["low"]),
            close=float(v["close"]),
            volume=int(float(v.get("volume") or 0)),
        )
        candles.append(normalize_ohlc(candle, "Twelve Data"))
    candles.sort(key=lambda c: c.time)
    return candles


def event_window(schedule, minutes: int) -> Callable[[datetime], bool]:
    """True for ``minutes`` after a session open or the daily close/reopen."""
    window = timedelta(minutes=minutes)

    def is_fast(now: datetime) -> bool:
        occ = schedule.latest_open(now)
        if occ is not None and timedelta(0) <= now - occ.start < window:
            return True
        local = now.astimezone(NEW_YORK)
        for hour in (17, 18):  # FXCM daily candle opens 17:00 NY; gold reopens ~18:00 NY
            event = local.replace(hour=hour, minute=0, second=0, microsecond=0)
            if timedelta(0) <= local - event < window:
                return True
        return False

    return is_fast


class TwelveDataProvider:
    name = "Twelve Data"

    def __init__(
        self,
        api_key: str,
        symbol: str = "XAU/USD",
        digits: int = 0,  # 0 = detect from the data
        refresh_seconds: int = 150,
        fast_refresh_seconds: int = 20,
        fast_window: Callable[[datetime], bool] | None = None,
        closed_refresh_seconds: int = 1800,
        max_staleness_seconds: int = 900,
        m5_keep_days: int = 10,
        clock: Callable[[], datetime] = utc_now,
        timeout: float = 30.0,
        retries: int = 3,
        retry_delay: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.symbol = symbol
        self.digits = digits
        self._detected_digits = 0
        self.refresh_seconds = refresh_seconds
        self.fast_refresh_seconds = fast_refresh_seconds
        self.closed_refresh_seconds = closed_refresh_seconds
        self.max_staleness = timedelta(seconds=max_staleness_seconds)
        self.m5_keep = timedelta(days=m5_keep_days)
        self._fast_window = fast_window or (lambda now: False)
        self._clock = clock
        self._retries = max(1, retries)
        self._retry_delay = retry_delay
        self._client = httpx.AsyncClient(
            base_url=BASE_URL, timeout=timeout, transport=transport, headers={"Authorization": f"apikey {api_key}"}
        )
        self._lock = asyncio.Lock()
        self._m5: dict[datetime, Candle] = {}
        self._h1: dict[datetime, Candle] = {}
        self._bootstrapped = False
        self._last_success: datetime | None = None
        self._next_attempt: datetime | None = None
        self._failures = 0
        self._last_error: str | None = None
        self.credits_used = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    async def _series(self, interval: str, outputsize: int) -> list[Candle]:
        params = {
            "symbol": self.symbol,
            "interval": interval,
            "outputsize": max(1, min(outputsize, MAX_OUTPUTSIZE)),
            "timezone": "UTC",
        }
        last_error = "unknown error"
        for attempt in range(1, self._retries + 1):
            try:
                response = await self._client.get("/time_series", params=params)
                self.credits_used += 1
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = f"Twelve Data unreachable: {exc!r}"
            else:
                if response.status_code == 200 and payload.get("status") == "ok":
                    values = payload.get("values", [])
                    self._detected_digits = max(self._detected_digits, price_decimals(values))
                    return parse_values(values)
                code = payload.get("code", response.status_code)
                last_error = f"Twelve Data error {code}: {payload.get('message', response.text)[:200]}"
                if code in (400, 401, 403, 404, 429):
                    break  # bad request / bad key / out of credits: retrying now only wastes credits
            if attempt < self._retries:
                await asyncio.sleep(self._retry_delay * 2 ** (attempt - 1))
        raise DataUnavailable(last_error)

    def _interval(self, now: datetime) -> timedelta:
        if market_closed(now):
            return timedelta(seconds=self.closed_refresh_seconds)
        if self._fast_window(now):
            return timedelta(seconds=self.fast_refresh_seconds)
        return timedelta(seconds=self.refresh_seconds)

    async def refresh(self, force: bool = False) -> None:
        async with self._lock:
            now = self._clock()
            if not force:
                if self._next_attempt and now < self._next_attempt:
                    return self._raise_if_stale(now)
                if self._last_success and now - self._last_success < self._interval(now):
                    return
            try:
                if not self._bootstrapped:
                    h1 = await self._series("1h", MAX_OUTPUTSIZE)
                    m5 = await self._series("5min", MAX_OUTPUTSIZE)
                    self._h1 = {c.time: c for c in h1}
                    self._bootstrapped = True
                    log.info("Twelve Data history loaded: %d 1h and %d 5min %s candles", len(h1), len(m5), self.symbol)
                else:
                    gap_minutes = (now - self._last_success).total_seconds() / 60 if self._last_success else 60
                    m5 = await self._series("5min", int(gap_minutes // 5) + 3)
            except DataUnavailable as exc:
                self._failures += 1
                self._last_error = str(exc)
                delay = min(600, 20 * 2 ** (self._failures - 1))
                self._next_attempt = now + timedelta(seconds=delay)
                log.warning("Twelve Data refresh failed (%s); next attempt in %ss", exc, delay)
                return self._raise_if_stale(now)
            for candle in m5:  # the newest (forming) candle is replaced on every refresh
                self._m5[candle.time] = candle
            self._rebuild_h1_from_m5()
            cutoff = now - self.m5_keep
            self._m5 = {t: c for t, c in self._m5.items() if t >= cutoff}
            self._last_success, self._next_attempt, self._failures, self._last_error = now, None, 0, None

    def _raise_if_stale(self, now: datetime) -> None:
        if self._last_success is None or now - self._last_success > self.max_staleness:
            raise DataUnavailable(self._last_error or "no Twelve Data refresh yet")

    def _rebuild_h1_from_m5(self) -> None:
        """Recent hours come from 5min candles, so the forming hour stays current."""
        m5 = sorted(self._m5.values(), key=lambda c: c.time)
        for candle in aggregate(m5, Timeframe.H1):
            self._h1[candle.time] = candle

    # ------------------------------------------------------------------ #
    def _series_for(self, timeframe: Timeframe) -> list[Candle]:
        m5 = sorted(self._m5.values(), key=lambda c: c.time)
        h1 = sorted(self._h1.values(), key=lambda c: c.time)
        if timeframe is Timeframe.M5:
            return m5
        if timeframe is Timeframe.M15:
            return aggregate(m5, Timeframe.M15)
        if timeframe is Timeframe.H1:
            return h1
        return aggregate(h1, timeframe)

    async def get_candles(
        self,
        timeframe: Timeframe,
        count: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        await self.refresh()
        candles = self._series_for(timeframe)
        if start is not None:
            first = bucket_start(timeframe, start)
            candles = [c for c in candles if c.time >= first]
        if end is not None:
            candles = [c for c in candles if c.time <= end]
        if count is not None:
            candles = candles[-count:]
        if not candles:
            raise DataUnavailable(f"no {timeframe.label} candles from Twelve Data for {self.symbol}")
        return candles

    async def get_price(self) -> Price:
        await self.refresh()
        if not self._m5:
            raise DataUnavailable("no Twelve Data price yet")
        latest = self._m5[max(self._m5)]
        return Price(
            instrument=self.symbol,
            bid=latest.close,
            ask=latest.close,
            time=self._last_success,
            digits=self.price_digits,
            point_size=10 ** -self.price_digits,
        )

    @property
    def price_digits(self) -> int:
        return self.digits or self._detected_digits or 2

    async def health(self) -> dict:
        try:
            await self.refresh()
        except DataUnavailable:
            pass
        now = self._clock()
        fresh = self._last_success is not None and now - self._last_success <= self.max_staleness
        return {
            "connected": fresh,
            "status": "CONNECTED" if fresh else "STALE",
            "provider": self.name,
            "connection": "free plan",
            "instrument": self.symbol,
            "last_price_received_at": self._last_success.isoformat() if self._last_success else None,
            "reconnects": self._failures,
            "credits_used_since_start": self.credits_used,
            "last_error": self._last_error,
        }
