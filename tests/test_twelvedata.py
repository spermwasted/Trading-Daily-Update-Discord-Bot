"""Candle aggregation on FXCM boundaries and the Twelve Data provider cache."""
import asyncio
from datetime import timedelta

import httpx
import pytest

from gold_bot.data.aggregate import aggregate, bucket_start
from gold_bot.data.provider import DataUnavailable
from gold_bot.data.twelvedata import TwelveDataProvider, event_window
from gold_bot.models import Timeframe
from tests.helpers import c, dt, series
from tests.test_time_sessions import SCHEDULE


# ------------------------------- aggregation -------------------------------- #
def test_d1_bucket_is_17_00_new_york_across_dst():
    assert bucket_start(Timeframe.D1, dt("2026-07-15T12:00:00Z")) == dt("2026-07-14T21:00:00Z")  # EDT
    assert bucket_start(Timeframe.D1, dt("2026-12-15T23:00:00Z")) == dt("2026-12-15T22:00:00Z")  # EST


def test_h4_buckets_align_to_trading_day_open():
    # Winter (EST): 17,21,01,05,09,13 NY = 22,02,06,10,14,18 UTC
    assert bucket_start(Timeframe.H4, dt("2026-12-15T23:30:00Z")) == dt("2026-12-15T22:00:00Z")
    assert bucket_start(Timeframe.H4, dt("2026-12-16T05:59:00Z")) == dt("2026-12-16T02:00:00Z")
    assert bucket_start(Timeframe.H4, dt("2026-12-16T21:59:00Z")) == dt("2026-12-16T18:00:00Z")
    # Summer (EDT): 17 NY = 21 UTC
    assert bucket_start(Timeframe.H4, dt("2026-07-15T00:10:00Z")) == dt("2026-07-14T21:00:00Z")


def test_aggregate_h1_into_d1_is_exact_and_drops_partial_head():
    start = dt("2026-11-09T20:00:00Z")  # 15:00 EST: mid trading day -> partial bucket
    h1 = series(start, timedelta(hours=1), [(10 + i, 12 + i, 9 + i, 11 + i) for i in range(30)])
    days = aggregate(h1, Timeframe.D1)
    assert days[0].time == dt("2026-11-09T22:00:00Z")  # partial 20:00-22:00 bucket dropped
    first_day = [x for x in h1 if dt("2026-11-09T22:00:00Z") <= x.time < dt("2026-11-10T22:00:00Z")]
    assert days[0].open == first_day[0].open and days[0].close == first_day[-1].close
    assert days[0].high == max(x.high for x in first_day) and days[0].low == min(x.low for x in first_day)


def test_aggregate_m5_into_m15():
    m5 = series(dt("2026-11-10T08:00:00Z"), timedelta(minutes=5), [(1, 3, 0.5, 2), (2, 4, 1, 3), (3, 3.5, 2, 2.5)])
    (bar,) = aggregate(m5, Timeframe.M15)
    assert (bar.open, bar.high, bar.low, bar.close) == (1, 4, 0.5, 2.5)


# ------------------------------- provider ----------------------------------- #
class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _values(candles):
    return [{"datetime": x.time.strftime("%Y-%m-%d %H:%M:%S"), "open": str(x.open), "high": str(x.high),
             "low": str(x.low), "close": str(x.close)} for x in reversed(candles)]


def make_feed(now):
    m5 = series(dt("2026-11-09T22:00:00Z"), timedelta(minutes=5),
                [(4000 + i, 4001 + i, 3999 + i, 4000.5 + i) for i in range(int((now - dt("2026-11-09T22:00:00Z")).total_seconds() // 300) + 1)])
    h1 = aggregate(m5, Timeframe.H1)
    return m5, h1


def provider_with(now, handler_state, **kw):
    requests = []

    def handler(request):
        requests.append(request)
        if handler_state.get("fail"):
            return httpx.Response(429, json={"code": 429, "message": "You have run out of API credits", "status": "error"})
        interval = request.url.params["interval"]
        size = int(request.url.params["outputsize"])
        data = handler_state["m5"] if interval == "5min" else handler_state["h1"]
        return httpx.Response(200, json={"meta": {}, "values": _values(data[-size:]), "status": "ok"})

    clock = Clock(now)
    p = TwelveDataProvider("key", transport=httpx.MockTransport(handler), clock=clock, retry_delay=0, **kw)
    return p, requests, clock


def test_bootstrap_then_incremental_refresh_and_cache_hits():
    now = dt("2026-11-10T08:02:00Z")
    m5, h1 = make_feed(now)
    state = {"m5": m5, "h1": h1}
    p, requests, clock = provider_with(now, state)
    d1 = asyncio.run(p.get_candles(Timeframe.D1, count=5))
    assert len(requests) == 2  # 1h + 5min history
    assert requests[0].headers["Authorization"] == "apikey key" and "apikey" not in str(requests[0].url)
    assert d1[-1].time == dt("2026-11-09T22:00:00Z")
    asyncio.run(p.get_candles(Timeframe.H4))
    asyncio.run(p.get_price())
    assert len(requests) == 2  # served from cache

    clock.now = now + timedelta(seconds=160)  # past the 150 s refresh interval
    state["m5"], state["h1"] = make_feed(clock.now)
    price = asyncio.run(p.get_price())
    assert len(requests) == 3 and int(requests[-1].url.params["outputsize"]) <= 6
    assert price.bid == state["m5"][-1].close


def test_fast_refresh_window_after_session_open():
    london_open = dt("2026-11-10T08:00:00Z")
    fast = event_window(SCHEDULE, 4)
    assert fast(london_open + timedelta(minutes=1)) and not fast(london_open + timedelta(minutes=10))
    assert fast(dt("2026-11-10T23:01:00Z"))  # 18:01 New York (gold reopens)


def test_failures_back_off_and_serve_cache_until_stale():
    now = dt("2026-11-10T08:02:00Z")
    m5, h1 = make_feed(now)
    state = {"m5": m5, "h1": h1}
    p, requests, clock = provider_with(now, state, max_staleness_seconds=900)
    asyncio.run(p.get_price())
    state["fail"] = True
    clock.now = now + timedelta(seconds=200)
    asyncio.run(p.get_price())  # refresh fails, cached data still fresh enough
    calls = len(requests)
    clock.now += timedelta(seconds=5)
    asyncio.run(p.get_price())
    assert len(requests) == calls  # backing off, no wasted credits
    clock.now = now + timedelta(seconds=1200)
    with pytest.raises(DataUnavailable, match="credits"):
        asyncio.run(p.get_price())


def test_bad_key_raises_without_retries():
    p, requests, _ = provider_with(dt("2026-11-10T08:02:00Z"), {"fail": True}, retries=3)
    with pytest.raises(DataUnavailable):
        asyncio.run(p.get_candles(Timeframe.M5, count=1))
    assert len(requests) == 1
