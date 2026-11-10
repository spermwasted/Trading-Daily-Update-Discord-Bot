import asyncio
import io
import math
from datetime import datetime, timezone, timedelta

import pytest
from PIL import Image


# ── fixtures ──────────────────────────────────────────────────────────────────
def yahoo_payload(closes, symbol="TEST", currency="USD", start=1_700_000_000, step=86400, kind="EQUITY"):
    n = len(closes)
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": symbol, "currency": currency, "longName": f"{symbol} Inc", "instrumentType": kind,
                 "exchangeTimezoneName": "America/New_York", "fullExchangeName": "NasdaqGS",
                 "regularMarketPrice": closes[-1], "chartPreviousClose": closes[0],
                 "fiftyTwoWeekHigh": max(closes), "fiftyTwoWeekLow": min(closes)},
        "timestamp": [start + i * step for i in range(n)],
        "indicators": {"quote": [{
            "open": [c * 0.99 for c in closes], "high": [c * 1.01 for c in closes],
            "low": [c * 0.98 for c in closes], "close": list(closes), "volume": [1000 + i for i in range(n)]}]}}]}}


def wave(n=260, base=100.0):
    return [base + 10 * math.sin(i / 9) + i * 0.05 for i in range(n)]


# ── version system ────────────────────────────────────────────────────────────
def test_version_constants(bot_mod):
    assert bot_mod.BOT_CHANGELOG[0]["version"] == bot_mod.BOT_VERSION
    assert bot_mod.BOT_CHANGELOG[0]["build"] == bot_mod.BOT_BUILD
    builds = [e["build"] for e in bot_mod.BOT_CHANGELOG]
    assert builds == sorted(builds, reverse=True)
    assert bot_mod._vs_tuple("6.0.0") > bot_mod._vs_tuple("5.9.12")
    assert bot_mod._vs_find("v6.0.0") is bot_mod.BOT_CHANGELOG[0]
    assert bot_mod._vs_find("bot-47")["build"] == 47


def test_version_boot_record(bot_mod):
    st = bot_mod._VS_STATE
    st.clear()
    assert bot_mod._vs_record_boot()[1] == "fresh"
    assert bot_mod._vs_record_boot()[1] == "same"
    st["current"] = "5.0.0"
    prev, kind = bot_mod._vs_record_boot()
    assert (prev, kind) == ("5.0.0", "upgrade")
    st["current"] = "9.0.0"
    assert bot_mod._vs_record_boot()[1] == "downgrade"


def test_commands_registered(bot_mod):
    for name in ("version", "changelog", "market", "stock", "chart", "compare", "technicals", "pivots", "movers",
                 "feargreed", "marketclock", "watchlist", "palert", "palerts", "pbuy", "psell", "portfolio",
                 "ptrades", "pleaderboard", "paperreset", "positionsize", "pnl", "riskreward", "compound",
                 "marketdaily"):
        assert bot_mod.bot.get_command(name) is not None, name
    assert bot_mod.bot.get_command("marketdaily setup") is not None
    assert bot_mod.bot.get_command("watchlist add") is not None
    assert bot_mod._MD_SKIPPED == []
    assert "market_desk" in bot_mod.HELP_PAGES


# ── symbols / parsing ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,first", [
    ("btc", "BTC-USD"), ("$AAPL", "AAPL"), ("eurusd", "EURUSD=X"), ("EUR/USD", "EURUSD=X"), ("gold", "GC=F"),
    ("spx", "^GSPC"), ("BTCUSDT", "BTC-USD"), ("reliance.ns", "RELIANCE.NS"), ("eth/usd", "ETH-USD"),
])
def test_candidates(bot_mod, raw, first):
    assert bot_mod.md_candidates(raw)[0] == first


def test_parse_chart_drops_null_bars(bot_mod):
    p = yahoo_payload([10, 11, 12, 13])
    p["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None
    s = bot_mod.md_parse_chart(p, "TEST")
    assert s.c == [10, 12, 13] and len(s.ts) == 3 and s.price == 13
    assert s.name == "TEST Inc" and s.hi52 == 13


def test_parse_chart_error(bot_mod):
    with pytest.raises(bot_mod.MarketError):
        bot_mod.md_parse_chart({"chart": {"result": None, "error": {"description": "No data found"}}}, "ZZZ")


def test_parse_range(bot_mod):
    assert bot_mod.md_parse_range("1w") == "5d"
    assert bot_mod.md_parse_range("6M") == "6mo"
    with pytest.raises(bot_mod.MarketError):
        bot_mod.md_parse_range("7years")


def test_parse_amount(bot_mod):
    assert bot_mod.md_parse_amount("10") == ("qty", 10)
    assert bot_mod.md_parse_amount("$500") == ("usd", 500)
    assert bot_mod.md_parse_amount("25%") == ("pct", 25)
    assert bot_mod.md_parse_amount("all") == ("pct", 100)
    assert bot_mod.md_parse_amount("1.5k") == ("qty", 1500)
    for bad in ("-5", "abc", "150%", "nan"):
        with pytest.raises(bot_mod.MarketError):
            bot_mod.md_parse_amount(bad)


def test_parse_alert(bot_mod):
    assert bot_mod.md_parse_alert(["BTC", ">", "70k"]) == ("BTC", "above", 70000)
    assert bot_mod.md_parse_alert(["AAPL", "below", "$150"]) == ("AAPL", "below", 150)
    assert bot_mod.md_parse_alert(["TSLA", "move", "5%"]) == ("TSLA", "move", 5)
    with pytest.raises(bot_mod.MarketError):
        bot_mod.md_parse_alert(["BTC", "sideways", "1"])
    hit = bot_mod.md_alert_hit
    assert hit({"op": "above", "target": 10}, 10) and not hit({"op": "above", "target": 10}, 9.9)
    assert hit({"op": "below", "target": 10}, 9) and not hit({"op": "below", "target": 10}, 11)
    assert hit({"op": "move", "target": 5, "ref": 100}, 94.9) and not hit({"op": "move", "target": 5, "ref": 100}, 103)


# ── indicators ────────────────────────────────────────────────────────────────
def test_sma_ema(bot_mod):
    xs = [1, 2, 3, 4, 5]
    assert bot_mod.md_sma(xs, 3) == [None, None, 2, 3, 4]
    e = bot_mod.md_ema(xs, 3)
    assert e[:2] == [None, None] and e[2] == 2 and e[3] == pytest.approx(3) and e[4] == pytest.approx(4)


def test_rsi_bounds(bot_mod):
    up = list(range(1, 40))
    assert bot_mod.md_rsi(up)[-1] == 100.0
    down = list(range(40, 1, -1))
    assert bot_mod.md_rsi(down)[-1] == pytest.approx(0.0)
    r = [x for x in bot_mod.md_rsi(wave()) if x is not None]
    assert all(0 <= x <= 100 for x in r)


def test_rsi_reference_value(bot_mod):
    # classic Wilder example series (StockCharts) → RSI(14) on bar 15 = 70.46 (their spreadsheet)
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert bot_mod.md_rsi(closes, 14)[14] == pytest.approx(70.46, abs=0.01)


def test_macd_bbands_atr_stoch(bot_mod):
    c = wave()
    h = [x * 1.01 for x in c]
    l = [x * 0.99 for x in c]
    line, sig, hist = bot_mod.md_macd(c)
    assert line[24] is None and line[25] is not None and sig[33] is not None
    assert hist[-1] == pytest.approx(line[-1] - sig[-1])
    mid, up, lo = bot_mod.md_bbands(c)
    assert lo[-1] < mid[-1] < up[-1]
    atr = bot_mod.md_atr(h, l, c)
    assert atr[14] is not None and atr[-1] > 0
    k, d = bot_mod.md_stoch(h, l, c)
    assert 0 <= k[-1] <= 100 and d[-1] is not None


def test_risk_stats_and_pivots(bot_mod):
    assert bot_mod.md_max_drawdown([100, 120, 60, 130]) == pytest.approx(-50)
    assert bot_mod.md_volatility([100] * 10) == 0
    pv = bot_mod.md_pivots(110, 90, 100)
    assert pv["classic"]["P"] == pytest.approx(100)
    assert pv["classic"]["R1"] == pytest.approx(110) and pv["classic"]["S1"] == pytest.approx(90)
    assert pv["fib"]["R1"] == pytest.approx(107.64)


def test_analyze_trend_direction(bot_mod):
    up = bot_mod.md_parse_chart(yahoo_payload([100 * 1.004 ** i for i in range(260)]), "UP")
    dn = bot_mod.md_parse_chart(yahoo_payload([100 * 0.996 ** i for i in range(260)]), "DN")
    a_up, a_dn = bot_mod.md_analyze(up), bot_mod.md_analyze(dn)
    assert a_up["score"] > 0 > a_dn["score"]
    assert -100 <= a_dn["score"] <= 100 and a_up["reasons"]


# ── calculators ───────────────────────────────────────────────────────────────
def test_calculators(bot_mod):
    r = bot_mod.md_position_size(10_000, 1, 100, 95)
    assert r["risk"] == 100 and r["units"] == pytest.approx(20) and r["side"] == "long"
    rr = bot_mod.md_risk_reward(100, 95, 115)
    assert rr["rr"] == pytest.approx(3) and rr["breakeven_winrate"] == pytest.approx(25)
    with pytest.raises(bot_mod.MarketError):
        bot_mod.md_risk_reward(100, 95, 90)
    c = bot_mod.md_compound(1000, 12, 1)
    assert c["fv"] == pytest.approx(1000 * 1.01 ** 12)
    assert bot_mod.md_compound(1000, 0, 2, 100)["fv"] == pytest.approx(3400)


def test_formatting(bot_mod):
    assert bot_mod.md_fmt_price(1234.5) == "1,234.50"
    assert bot_mod.md_fmt_price(1.23456) == "1.2346"
    assert bot_mod.md_fmt_price(0.00012345) == "0.0001234" or bot_mod.md_fmt_price(0.00012345).startswith("0.000123")
    assert bot_mod.md_fmt_big(2_500_000_000) == "2.50B"
    assert bot_mod.md_money(-5) == "-$5.00"


# ── scheduling ────────────────────────────────────────────────────────────────
def test_brief_due(bot_mod):
    cfg = {"enabled": True, "channel_id": 1, "time": "09:30", "tz": "America/New_York", "weekdays_only": True, "last": None}
    # Tue 10 Nov 2026 14:40 UTC = 09:40 New York
    t = datetime(2026, 11, 10, 14, 40, tzinfo=timezone.utc)
    assert bot_mod.md_brief_due(cfg, t)
    assert not bot_mod.md_brief_due(cfg, t - timedelta(minutes=20))          # before schedule
    assert not bot_mod.md_brief_due(cfg, t + timedelta(hours=4))             # outside catch-up window
    assert not bot_mod.md_brief_due({**cfg, "last": "2026-11-10"}, t)        # already posted
    assert not bot_mod.md_brief_due(cfg, datetime(2026, 11, 14, 14, 40, tzinfo=timezone.utc))   # Saturday
    assert bot_mod.md_brief_due({**cfg, "weekdays_only": False}, datetime(2026, 11, 14, 14, 40, tzinfo=timezone.utc))
    assert not bot_mod.md_brief_due({**cfg, "enabled": False}, t)


def test_session_state(bot_mod):
    open_, _, nxt = bot_mod.md_session_state("America/New_York", (9, 30), (16, 0),
                                             datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc))
    assert open_ and nxt.hour == 16
    closed, _, nxt = bot_mod.md_session_state("America/New_York", (9, 30), (16, 0),
                                              datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc))   # Saturday
    assert not closed and nxt.weekday() == 0


def test_time_parsing(bot_mod):
    assert bot_mod._md_parse_hhmm("9am") == "09:00"
    assert bot_mod._md_parse_hhmm("4:05 pm") == "16:05"
    assert bot_mod._md_parse_hhmm("23:59") == "23:59"
    assert bot_mod._md_check_tz("pkt") == "Asia/Karachi"
    with pytest.raises(bot_mod.MarketError):
        bot_mod._md_parse_hhmm("25:00")


# ── rendering ─────────────────────────────────────────────────────────────────
def _png_ok(data, size=None):
    im = Image.open(io.BytesIO(data))
    im.load()
    if size:
        assert im.size == size
    return im


def test_render_charts(bot_mod):
    s = bot_mod.md_parse_chart(yahoo_payload(wave(120)), "TEST")
    _png_ok(bot_mod.md_render_chart(s, "6mo", bot_mod.md_analyze(s)), (1280, 760))          # candles
    long = bot_mod.md_parse_chart(yahoo_payload(wave(400)), "LONG")
    _png_ok(bot_mod.md_render_chart(long, "2y"), (1280, 760))                              # area line
    tiny = bot_mod.md_parse_chart(yahoo_payload([5.0]), "ONE")
    _png_ok(bot_mod.md_render_chart(tiny, "1d"))                                           # degenerate
    flat = bot_mod.md_parse_chart(yahoo_payload([3.0] * 50), "FLAT")
    _png_ok(bot_mod.md_render_chart(flat, "3mo"))
    _png_ok(bot_mod.md_render_compare([s, long], "1y"), (1280, 640))
    _png_ok(bot_mod.md_render_heat([("AAPL", "190.00", 1.2), ("BTC", "70,000", -6.0), ("X", "n/a", None)], "T", "S"))


# ── async data layer with a fake provider ─────────────────────────────────────
def test_series_resolution_and_fx(bot_mod, monkeypatch):
    calls = []

    async def fake_json(url, params=None, ttl=60):
        calls.append(url)
        sym = url.rsplit("/", 1)[-1]
        if sym == "BTC":
            raise bot_mod.MarketError("Nothing found for that.")
        if sym == "BTC-USD":
            return yahoo_payload([60000, 61000], "BTC-USD", kind="CRYPTOCURRENCY")
        if sym == "GBPUSD%3DX":
            return yahoo_payload([1.25, 1.26], "GBPUSD=X")
        return yahoo_payload([100, 101], sym)

    monkeypatch.setattr(bot_mod, "_md_json", fake_json)
    bot_mod._MD_RESOLVED.clear()
    bot_mod._MD_FX_CACHE.clear()

    async def run():
        s = await bot_mod.md_series("btc", "5d")
        assert s.symbol == "BTC-USD" and s.display == "BTC"
        assert bot_mod._MD_RESOLVED["BTC"] == "BTC-USD"
        q = await bot_mod.md_quote_daily("AAPL")
        assert q.pct == pytest.approx(1.0)
        assert await bot_mod.md_to_usd("USD") == 1
        assert await bot_mod.md_to_usd("GBp") == pytest.approx(0.0126)
        res = await bot_mod.md_many(["AAPL", "MSFT"])
        assert all(not isinstance(v, Exception) for v in res.values())

    asyncio.run(run())
    assert not any("BTC-USD" in u and "query2" in u for u in calls)


def test_paper_portfolio_value(bot_mod, monkeypatch):
    async def fake_price(raw):
        return None, {"AAPL": 110.0, "BTC-USD": 50000.0}[raw]

    monkeypatch.setattr(bot_mod, "_md_price_usd", fake_price)
    pf = {"cash": 1000.0, "start": 100_000.0, "pos": {"AAPL": {"qty": 10, "avg": 100.0},
                                                       "BTC-USD": {"qty": 0.5, "avg": 40000.0}}}
    equity, rows, failed = asyncio.run(bot_mod.md_portfolio_value(pf))
    assert equity == pytest.approx(1000 + 1100 + 25000)
    assert rows[0][0] == "BTC-USD" and rows[0][5] == pytest.approx(5000)
    assert not failed


def test_views_and_brief_build(bot_mod, monkeypatch):
    async def fake_json(url, params=None, ttl=60):
        if "alternative.me" in url:
            return {"data": [{"value": "62", "value_classification": "Greed", "timestamp": "1"}] * 7}
        if "coingecko" in url:
            return [{"symbol": f"c{i}", "current_price": 1 + i, "price_change_percentage_24h": i - 50} for i in range(100)]
        sym = bot_mod._up.unquote(url.rsplit("/", 1)[-1])
        if sym.startswith("NVDA"):
            raise bot_mod.MarketError("Nothing found for that.")
        return yahoo_payload(wave(260), sym)

    monkeypatch.setattr(bot_mod, "_md_json", fake_json)
    bot_mod._MD_RESOLVED.clear()

    async def run():
        v = bot_mod._VSChangelogView(1, 0)
        assert len(v.children) == 1
        cv = bot_mod._MDChartView(1, "AAPL", "6mo", "## hdr", bot_mod.C_GREEN)
        assert cv.rng == "6mo"
        header, color, png = await bot_mod._md_chart_payload("AAPL", "1y")
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and "AAPL" in header
        cfg = {"symbols": ["AAPL", "NVDA", "BTC-USD"], "tz": "Asia/Karachi"}
        e, png = await bot_mod.md_build_brief(cfg)
        names = [f.name for f in e.fields]
        assert "Server watchlist" in names and "Crypto movers 24h" in names and "Sessions" in names
        wl = next(f.value for f in e.fields if f.name == "Server watchlist")
        assert "NVDA** — n/a" in wl                       # one dead symbol fails soft
        assert "Greed" in e.description

    asyncio.run(run())
