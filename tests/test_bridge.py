"""Tests for the FXCM bridge code (runs without the ForexConnect SDK by
injecting a stand-in SDK object) and for the bot's bridge client."""
import asyncio
import json
import threading
import types
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import numpy as np
import pytest

from fxcm_bridge import feed as feed_mod
from fxcm_bridge.config import BridgeConfig, ConfigError
from fxcm_bridge.server import serve
from gold_bot.data.bridge_client import BridgeClient
from gold_bot.data.provider import DataUnavailable
from gold_bot.models import Timeframe

ROOT = Path(__file__).resolve().parents[1]


def test_bridge_never_references_order_functions():
    source = "\n".join(p.read_text() for p in (ROOT / "fxcm_bridge").glob("*.py"))
    for forbidden in ("create_order_request", "CreateOrder", "OpenPosition", "send_request", "ORDERS", "TRADES"):
        assert forbidden not in source


def test_config_requires_credentials(monkeypatch):
    monkeypatch.delenv("FXCM_USERNAME", raising=False)
    monkeypatch.delenv("FXCM_PASSWORD", raising=False)
    for name in ("FXCM_ENV", "FXCM_CONNECTION", "FXCM_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError):
        BridgeConfig().validate()
    monkeypatch.setenv("FXCM_USERNAME", "demo-user")
    monkeypatch.setenv("FXCM_PASSWORD", "secret")
    cfg = BridgeConfig().validate()
    assert "secret" not in json.dumps(cfg.describe())
    assert cfg.connection == "Demo" and cfg.url.startswith("https://")
    monkeypatch.setenv("FXCM_ENV", "real")
    assert BridgeConfig().connection == "Real"


def test_history_rows_to_candles_uses_bid_prices():
    dtype = np.dtype([("Date", "M8[ns]"), ("BidOpen", "f8"), ("BidHigh", "f8"), ("BidLow", "f8"), ("BidClose", "f8"),
                      ("AskOpen", "f8"), ("AskHigh", "f8"), ("AskLow", "f8"), ("AskClose", "f8"), ("Volume", "i4")])
    rows = np.zeros(1, dtype)
    rows[0] = (np.datetime64("2026-11-09T22:00:00"), 4100.111, 4120.5, 4090.25, 4110.0, 4100.5, 4121, 4090.6, 4110.4, 42)
    candle = feed_mod.history_rows_to_candles(rows, "bid", digits=2)[0]
    assert candle == {"time": "2026-11-09T22:00:00Z", "open": 4100.11, "high": 4120.5, "low": 4090.25,
                      "close": 4110.0, "volume": 42}


def test_backoff_is_exponential_and_capped():
    assert [feed_mod.backoff_delay(n, 5, 60) for n in range(1, 6)] == [5, 10, 20, 40, 60]


# --------------------------------------------------------------------------- #
# Fake ForexConnect SDK to exercise login, live price and reconnection logic
# --------------------------------------------------------------------------- #
class _Row:
    def __init__(self, bid):
        self.instrument, self.bid, self.ask, self.digits = "XAU/USD", bid, bid + 0.4, 2
        self.point_size, self.time, self.trading_status = 0.01, None, "O"


class _FakeFX:
    instances = []
    fail_logins = 0
    OFFERS = "offers"

    def __init__(self):
        self.status_cb = None
        self._com = types.SimpleNamespace(is_ready=True)
        self.session = object()
        self.logged_out = False
        _FakeFX.instances.append(self)

    def login(self, user, password, url, connection, session_id, pin, session_status_callback=None):
        if _FakeFX.fail_logins > 0:
            _FakeFX.fail_logins -= 1
            raise RuntimeError("login refused (fake)")
        self.status_cb = session_status_callback

    def get_table(self, name):
        return [_Row(4100.0)]

    def logout(self):
        self.logged_out = True


def _fake_sdk():
    status = types.SimpleNamespace(name="SESSION_LOST")
    sdk = types.SimpleNamespace(
        ForexConnect=_FakeFX,
        fxcorepy=types.SimpleNamespace(
            O2GTransport=types.SimpleNamespace(
                set_ca_info=lambda path: None, set_proxy=lambda h, p: None, set_number_of_reconnections=lambda n: None
            ),
        ),
        Common=types.SimpleNamespace(
            subscribe_table_updates=lambda table, on_change_callback=None: types.SimpleNamespace(unsubscribe=lambda: None)
        ),
    )
    return sdk, status


def _config(monkeypatch, tmp_path):
    monkeypatch.setenv("FXCM_USERNAME", "demo")
    monkeypatch.setenv("FXCM_PASSWORD", "pw")
    monkeypatch.setenv("FXCM_RECONNECT_BACKOFF_INITIAL", "0")
    monkeypatch.setenv("FXCM_HISTORY_CACHE_DIR", str(tmp_path))
    return BridgeConfig().validate()


def _wait(predicate, timeout=10):
    import time

    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_feed_retries_failed_login_then_reconnects_after_session_loss(monkeypatch, tmp_path):
    _FakeFX.instances, _FakeFX.fail_logins = [], 2
    sdk, lost = _fake_sdk()
    feed = feed_mod.ForexConnectFeed(_config(monkeypatch, tmp_path), sdk=sdk)
    feed.start()
    try:
        assert _wait(lambda: feed.health()["connected"])
        assert len(_FakeFX.instances) == 3  # two failed logins, then success
        assert feed.latest_price()["bid"] == 4100.0

        first = _FakeFX.instances[-1]
        first.status_cb(None, lost)  # FXCM reports the session lost
        assert _wait(lambda: len(_FakeFX.instances) == 4 and feed.health()["connected"])
        assert first.logged_out and feed.health()["reconnects"] == 1
    finally:
        feed.stop()


def test_http_api_is_read_only_and_reports_errors(monkeypatch, tmp_path):
    class StubFeed:
        def health(self):
            return {"connected": False, "status": "CONNECTING", "instrument": "XAU/USD", "price_side": "bid",
                    "candle_open_mode": "previous_close"}

        def latest_price(self):
            raise feed_mod.NotConnectedError("not connected")

        def get_candles(self, timeframe, count=None, date_from=None, date_to=None):
            if timeframe == "bad":
                raise feed_mod.BadRequestError("unsupported timeframe")
            return [{"time": "2026-11-09T22:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 3}]

    httpd = serve(StubFeed(), "127.0.0.1", 0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        def status(path, method="GET"):
            try:
                return opener.open(urllib.request.Request(base + path, method=method), timeout=5).status
            except urllib.error.HTTPError as exc:
                return exc.code

        assert status("/health") == 503
        assert status("/price") == 503
        assert status("/candles?timeframe=bad&count=5") == 400
        assert status("/candles?timeframe=H1&count=5") == 200
        assert status("/candles", method="POST") == 405
        body = json.loads(opener.open(base + "/candles?timeframe=H1&count=5", timeout=5).read())
        assert body["candles"][0]["close"] == 1.5 and body["price_side"] == "bid"
    finally:
        httpd.shutdown()


# --------------------------------------------------------------------------- #
# Bot-side bridge client
# --------------------------------------------------------------------------- #
def test_bridge_client_retries_then_raises_data_unavailable():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={"error": "FXCM session is not connected"})

    client = BridgeClient("http://bridge", retries=3, retry_delay=0, transport=httpx.MockTransport(handler))
    with pytest.raises(DataUnavailable, match="not connected"):
        asyncio.run(client.get_price())
    assert calls["n"] == 3


def test_bridge_client_parses_candles_and_rejects_bad_ohlc():
    good = {"candles": [{"time": "2026-11-09T22:00:00Z", "open": 4100, "high": 4120, "low": 4090, "close": 4110}]}
    bad = {"candles": [{"time": "2026-11-09T22:00:00Z", "open": 4100, "high": 4095, "low": 4090, "close": 4110}]}
    for payload, ok in ((good, True), (bad, False)):
        client = BridgeClient("http://bridge", retry_delay=0,
                              transport=httpx.MockTransport(lambda r, p=payload: httpx.Response(200, json=p)))
        if ok:
            candles = asyncio.run(client.get_candles(Timeframe.D1, count=1))
            assert candles[0].high == 4120 and candles[0].time.tzinfo is not None
        else:
            with pytest.raises(DataUnavailable):
                asyncio.run(client.get_candles(Timeframe.D1, count=1))
