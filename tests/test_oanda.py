"""OANDA provider tests against recorded-shape v20 responses (httpx MockTransport)."""
import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from gold_bot.data.oanda import OandaProvider, parse_time
from gold_bot.data.provider import DataUnavailable
from gold_bot.models import Timeframe


def make(handler, **kw):
    return OandaProvider("test-token", transport=httpx.MockTransport(handler), retry_delay=0, **kw)


def router(requests):
    def handler(request: httpx.Request):
        requests.append(request)
        path = request.url.path
        if path == "/v3/accounts":
            return httpx.Response(200, json={"accounts": [{"id": "101-001-1234567-001", "tags": []}]})
        if path.endswith("/instruments") and "instruments" in request.url.params:
            return httpx.Response(200, json={"instruments": [{"name": "XAU_USD", "displayPrecision": 3}]})
        if path.endswith("/candles"):
            return httpx.Response(200, json={"instrument": "XAU_USD", "granularity": "D", "candles": [
                {"complete": True, "volume": 10, "time": "2026-11-08T22:00:00.000000000Z",
                 "bid": {"o": "4040.100", "h": "4100.250", "l": "4030.000", "c": "4090.000"}},
                {"complete": False, "volume": 5, "time": "2026-11-09T22:00:00.000000000Z",
                 "bid": {"o": "4091.000", "h": "4095.000", "l": "4085.000", "c": "4092.500"}},
            ]})
        if path.endswith("/pricing"):
            return httpx.Response(200, json={"prices": [{
                "instrument": "XAU_USD", "time": "2026-11-10T06:50:01.123456789Z", "tradeable": True,
                "bids": [{"price": "4092.415", "liquidity": 100}], "asks": [{"price": "4092.735", "liquidity": 100}]}]})
        return httpx.Response(404, json={"errorMessage": "not found"})
    return handler


def test_parse_time_handles_nanoseconds():
    assert parse_time("2026-11-10T06:50:01.123456789Z") == datetime(2026, 11, 10, 6, 50, 1, 123456, tzinfo=timezone.utc)


def test_candles_request_bid_prices_aligned_to_new_york_17():
    requests = []
    p = make(router(requests))
    candles = asyncio.run(p.get_candles(Timeframe.D1, count=2))
    assert [c.close for c in candles] == [4090.0, 4092.5] and candles[0].high == 4100.25
    q = requests[-1].url.params
    assert (q["price"], q["granularity"], q["dailyAlignment"], q["alignmentTimezone"]) == (
        "B", "D", "17", "America/New_York")
    assert requests[-1].headers["Authorization"] == "Bearer test-token"
    assert requests[0].url.path == "/v3/accounts"  # account discovered automatically


def test_timeframe_mapping_and_range_query():
    requests = []
    p = make(router(requests), account_id="101-001-1234567-001")
    start = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    asyncio.run(p.get_candles(Timeframe.M5, start=start, end=end))
    q = requests[-1].url.params
    assert q["granularity"] == "M5" and q["from"].startswith("2026-11-09T00:00:00") and "count" not in q
    asyncio.run(p.get_candles(Timeframe.H4, count=10))
    assert requests[-1].url.params["granularity"] == "H4"


def test_live_price_uses_bid_and_instrument_precision():
    p = make(router([]))
    price = asyncio.run(p.get_price())
    assert (price.bid, price.ask, price.digits, price.instrument) == (4092.415, 4092.735, 3, "XAU/USD")


def test_bad_token_fails_fast_without_retries():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"errorMessage": "Insufficient authorization to perform request."})

    with pytest.raises(DataUnavailable, match="401"):
        asyncio.run(make(handler, retries=3).get_price())
    assert len(calls) == 1


def test_server_errors_are_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"errorMessage": "Service unavailable"})

    with pytest.raises(DataUnavailable):
        asyncio.run(make(handler, retries=3, account_id="x").get_candles(Timeframe.H1, count=5))
    assert len(calls) == 3


def test_inconsistent_ohlc_is_rejected():
    def handler(request):
        return httpx.Response(200, json={"candles": [{"time": "2026-11-09T22:00:00.000000000Z", "volume": 1,
                                                      "bid": {"o": "10", "h": "9", "l": "8", "c": "9.5"}}]})

    with pytest.raises(DataUnavailable):
        asyncio.run(make(handler, account_id="x").get_candles(Timeframe.D1, count=1))
