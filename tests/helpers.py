"""Test helpers. Candles here are hand-written test inputs for the pure
analysis functions; the bot itself only ever uses FXCM data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gold_bot.models import Candle, Price

UTC = timezone.utc


def c(time: datetime, o: float, h: float, low: float, cl: float) -> Candle:
    return Candle(time=time, open=o, high=h, low=low, close=cl, volume=1)


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def series(start: datetime, step: timedelta, ohlc: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [c(start + i * step, *v) for i, v in enumerate(ohlc)]


class FakeProvider:
    """In-memory MarketDataProvider for service tests."""

    def __init__(self) -> None:
        self.candles: dict[str, list[Candle]] = {}
        self.price: Price | None = None
        self.fail = False
        self.calls = 0

    async def get_candles(self, timeframe, count=None, start=None, end=None):
        from gold_bot.data.provider import DataUnavailable

        self.calls += 1
        if self.fail:
            raise DataUnavailable("bridge down (test)")
        data = list(self.candles.get(timeframe.value, []))
        if start is not None:
            data = [x for x in data if x.time >= start]
        if end is not None:
            data = [x for x in data if x.time <= end]
        return data[-count:] if count else data

    async def get_price(self):
        from gold_bot.data.provider import DataUnavailable

        if self.fail or self.price is None:
            raise DataUnavailable("no price (test)")
        return self.price

    async def health(self):
        return {"connected": not self.fail}


class FakePublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[int, object]] = []
        self.fail = False

    async def send(self, channel_id, embed):
        if self.fail:
            raise RuntimeError("discord down (test)")
        self.sent.append((channel_id, embed))
        return str(len(self.sent))
