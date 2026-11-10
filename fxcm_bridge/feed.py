"""Persistent, self-healing ForexConnect market-data feed (read-only).

* Logs in with ForexConnect (user ID + password + host-descriptor URL +
  connection name), following the official ForexConnect Python docs.
* Keeps the session alive in a supervisor thread and re-logs in with
  exponential backoff whenever the session is lost, login fails, or live prices
  stop arriving while the market should be open.
* Streams live prices from the OFFERS table and serves OHLC history via
  ``ForexConnect.get_history``.

This module never creates, changes or deletes orders. It is market data only.
Python 3.7 compatible.
"""
import datetime
import logging
import os
import threading
import time

log = logging.getLogger("fxcm_bridge.feed")

UTC = datetime.timezone.utc


class FeedError(Exception):
    """Base class for feed errors returned to API clients."""

    http_status = 502


class NotConnectedError(FeedError):
    http_status = 503


class InstrumentUnavailableError(FeedError):
    http_status = 404


class BadRequestError(FeedError):
    http_status = 400


# --------------------------------------------------------------------------- #
# Pure helpers (unit-testable without the ForexConnect SDK)
# --------------------------------------------------------------------------- #
def datetime64_to_utc(value):
    """Convert a numpy datetime64 (ForexConnect history 'Date') to aware UTC."""
    ns = int(value.astype("datetime64[ns]").astype("int64"))
    return datetime.datetime(1970, 1, 1, tzinfo=UTC) + datetime.timedelta(microseconds=ns // 1000)


def to_utc(value):
    """Normalise a datetime coming from ForexConnect (naive = UTC) to aware UTC."""
    if value is None:
        return None
    if hasattr(value, "astype"):
        return datetime64_to_utc(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso_utc(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def history_rows_to_candles(rows, price_side, digits=None):
    """Convert a ForexConnect history ndarray to plain candle dicts."""
    prefix = "Bid" if price_side == "bid" else "Ask"
    candles = []
    for row in rows:
        values = [float(row[prefix + part]) for part in ("Open", "High", "Low", "Close")]
        if digits is not None:
            values = [round(v, digits) for v in values]
        candles.append({
            "time": iso_utc(datetime64_to_utc(row["Date"])),
            "open": values[0],
            "high": values[1],
            "low": values[2],
            "close": values[3],
            "volume": int(row["Volume"]),
        })
    candles.sort(key=lambda c: c["time"])
    return candles


def backoff_delay(attempt, initial, maximum):
    return min(maximum, initial * (2 ** max(0, attempt - 1)))


# --------------------------------------------------------------------------- #
# Feed
# --------------------------------------------------------------------------- #
class ForexConnectFeed(object):
    def __init__(self, config, sdk=None, clock=time.time):
        self.cfg = config
        self._sdk = sdk  # injected for tests; lazily imported otherwise
        self._clock = clock
        self._lock = threading.RLock()
        self._history_lock = threading.Lock()
        self._stop = threading.Event()
        self._reconnect_requested = threading.Event()
        self._thread = None
        self._tearing_down = False

        self._fx = None
        self._offers_listener = None
        self._status = "STARTING"
        self._connected = False
        self._last_error = None
        self._last_login_at = None
        self._reconnects = 0
        self._price = None  # dict with bid/ask/time/digits
        self._price_received_at = None
        self._offer_meta = {}

    # ----- SDK access ----------------------------------------------------- #
    def _forexconnect(self):
        if self._sdk is None:
            import forexconnect  # noqa: WPS433  (only available in the 3.7 image)

            self._sdk = forexconnect
        return self._sdk

    # ----- lifecycle ------------------------------------------------------ #
    def start(self):
        self._thread = threading.Thread(target=self._supervise, name="fxcm-supervisor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._reconnect_requested.set()
        if self._thread:
            self._thread.join(timeout=30)
        self._teardown()

    def _supervise(self):
        attempt = 0
        while not self._stop.is_set():
            if not self._connected:
                attempt += 1
                try:
                    self._connect()
                    attempt = 0
                except Exception as exc:  # noqa: B902 - SDK raises many types
                    self._set_error("login failed: {}".format(exc))
                    self._teardown()
                    delay = backoff_delay(attempt, self.cfg.reconnect_backoff_initial, self.cfg.reconnect_backoff_max)
                    log.warning("FXCM login attempt %d failed (%s); retrying in %ss", attempt, exc, delay)
                    self._stop.wait(delay)
                    continue

            # Connected: watch for session loss / stale prices.
            self._reconnect_requested.wait(5)
            if self._stop.is_set():
                break
            reason = None
            if self._reconnect_requested.is_set():
                reason = "session status {}".format(self._status)
            elif self._price_is_stale():
                reason = "no price update for over {}s".format(self.cfg.stale_price_seconds)
            if reason:
                log.warning("Reconnecting to FXCM: %s", reason)
                self._reconnects += 1
                self._teardown()
                self._reconnect_requested.clear()

    def _connect(self):
        fc = self._forexconnect()
        fxcorepy = fc.fxcorepy
        transport = fxcorepy.O2GTransport
        if self.cfg.ca_file:
            transport.set_ca_info(self.cfg.ca_file)
        if self.cfg.proxy_host:
            transport.set_proxy(self.cfg.proxy_host, int(self.cfg.proxy_port))
        transport.set_number_of_reconnections(self.cfg.sdk_reconnect_attempts)
        if self.cfg.history_dir:
            os.makedirs(self.cfg.history_dir, exist_ok=True)

        log.info("Logging in to FXCM ForexConnect: %s", self.cfg.describe())
        fx = fc.ForexConnect()
        fx.login(
            self.cfg.username,
            self.cfg.password,
            self.cfg.url,
            self.cfg.connection,
            self.cfg.session_id,
            self.cfg.pin,
            session_status_callback=self._on_session_status,
        )
        with self._lock:
            self._fx = fx
            self._connected = True
            self._status = "CONNECTED"
            self._last_login_at = datetime.datetime.now(UTC)
            self._last_error = None

        offers = fx.get_table(fc.ForexConnect.OFFERS)
        found = False
        for row in offers:
            if row.instrument == self.cfg.instrument:
                self._update_price(row)
                found = True
        if not found:
            raise InstrumentUnavailableError(
                "{} is not in this account's OFFERS table (check symbol subscription)".format(self.cfg.instrument)
            )
        self._offers_listener = fc.Common.subscribe_table_updates(
            offers, on_change_callback=self._on_offer_changed
        )
        self._prepare_history(fx)
        # Status events emitted during the previous teardown/login are stale now.
        self._reconnect_requested.clear()
        log.info("FXCM connected; %s live price %s", self.cfg.instrument, self._price)

    def _prepare_history(self, fx):
        """Create the price-history communicator and wait (with a timeout) for it.

        ``ForexConnect.get_history`` otherwise busy-waits forever on a communicator
        that never becomes ready, so we create it ourselves and bound the wait.
        """
        fxcorepy = self._forexconnect().fxcorepy
        if getattr(fx, "_com", None) is None:
            fx._com = fxcorepy.PriceHistoryCommunicatorFactory.create_communicator(
                fx.session, self.cfg.history_dir
            )
        deadline = self._clock() + self.cfg.history_ready_timeout
        while not fx._com.is_ready:
            if self._clock() > deadline:
                raise FeedError("price history communicator not ready after {}s".format(self.cfg.history_ready_timeout))
            time.sleep(0.2)

    def _teardown(self):
        self._tearing_down = True
        try:
            self._do_teardown()
        finally:
            self._tearing_down = False

    def _do_teardown(self):
        with self._lock:
            fx, listener = self._fx, self._offers_listener
            self._fx = None
            self._offers_listener = None
            self._connected = False
        if listener is not None:
            try:
                listener.unsubscribe()
            except Exception as exc:  # noqa: B902
                log.debug("unsubscribe failed: %s", exc)
        if fx is not None:
            try:
                fx.logout()
            except Exception as exc:  # noqa: B902
                log.debug("logout failed: %s", exc)

    # ----- callbacks (called on ForexConnect threads) --------------------- #
    def _on_session_status(self, session, status):
        name = getattr(status, "name", None) or str(status).split(".")[-1]
        log.info("FXCM session status: %s", name)
        with self._lock:
            self._status = name
        if name in ("DISCONNECTED", "SESSION_LOST") and not self._stop.is_set() and not self._tearing_down:
            with self._lock:
                self._connected = False  # stop serving data from a dead session
            self._reconnect_requested.set()

    def _on_offer_changed(self, listener, row_id, row):
        try:
            if row.instrument == self.cfg.instrument:
                self._update_price(row)
        except Exception as exc:  # noqa: B902
            log.debug("offer update ignored: %s", exc)

    def _update_price(self, row):
        digits = int(getattr(row, "digits", 2) or 2)
        price = {
            "instrument": row.instrument,
            "bid": round(float(row.bid), digits),
            "ask": round(float(row.ask), digits),
            "time": iso_utc(to_utc(getattr(row, "time", None))),
            "digits": digits,
            "point_size": float(getattr(row, "point_size", 10 ** -digits) or 10 ** -digits),
            "trading_status": str(getattr(row, "trading_status", "")),
        }
        with self._lock:
            self._price = price
            self._price_received_at = datetime.datetime.now(UTC)

    # ----- helpers -------------------------------------------------------- #
    def _set_error(self, message):
        with self._lock:
            self._last_error = message

    def _price_is_stale(self):
        if not self.cfg.stale_price_seconds or self._price_received_at is None:
            return False
        if not market_should_be_open(datetime.datetime.now(UTC)):
            return False
        age = (datetime.datetime.now(UTC) - self._price_received_at).total_seconds()
        return age > self.cfg.stale_price_seconds

    # ----- public API ----------------------------------------------------- #
    def health(self):
        with self._lock:
            return {
                "connected": self._connected,
                "status": self._status,
                "instrument": self.cfg.instrument,
                "connection": self.cfg.connection,
                "last_login_at": iso_utc(self._last_login_at),
                "last_price_received_at": iso_utc(self._price_received_at),
                "reconnects": self._reconnects,
                "last_error": self._last_error,
                "price_side": self.cfg.price_side,
                "candle_open_mode": self.cfg.candle_open_mode,
            }

    def latest_price(self):
        with self._lock:
            if not self._connected:
                raise NotConnectedError("FXCM session is not connected ({})".format(self._status))
            if self._price is None:
                raise NotConnectedError("no live price received yet")
            price = dict(self._price)
            price["received_at"] = iso_utc(self._price_received_at)
            price["session_status"] = self._status
            return price

    def get_candles(self, timeframe, count=None, date_from=None, date_to=None):
        from fxcm_bridge.config import SUPPORTED_TIMEFRAMES

        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise BadRequestError("unsupported timeframe {!r}; use one of {}".format(timeframe, SUPPORTED_TIMEFRAMES))
        if count is not None and not (1 <= count <= self.cfg.max_quotes_per_request):
            raise BadRequestError("count must be between 1 and {}".format(self.cfg.max_quotes_per_request))
        if count is None and date_from is None:
            raise BadRequestError("give either count or from")

        with self._lock:
            fx = self._fx
            digits = (self._price or {}).get("digits")
            if not self._connected or fx is None:
                raise NotConnectedError("FXCM session is not connected ({})".format(self._status))
        fxcorepy = self._forexconnect().fxcorepy
        mode = (
            fxcorepy.O2GCandleOpenPriceMode.FIRST_TICK
            if self.cfg.candle_open_mode == "first_tick"
            else fxcorepy.O2GCandleOpenPriceMode.PREVIOUS_CLOSE
        )
        with self._history_lock:
            try:
                rows = fx.get_history(
                    self.cfg.instrument,
                    timeframe,
                    date_from,
                    date_to,
                    count if count is not None else -1,
                    candle_open_price_mode=mode,
                )
            except Exception as exc:  # noqa: B902
                if not self._connected:
                    raise NotConnectedError("FXCM disconnected during history request")
                raise FeedError("ForexConnect get_history failed: {}".format(exc))
        return history_rows_to_candles(rows, self.cfg.price_side, digits)


def market_should_be_open(now_utc):
    """Rough FX/metals weekly window: Sun 17:00 -> Fri 17:00 New York time.

    Only used by the stale-price watchdog; not used for any analysis.
    """
    ny = now_utc.astimezone(_new_york())
    weekday, hour = ny.weekday(), ny.hour  # Mon=0 .. Sun=6
    if weekday == 5:
        return False
    if weekday == 4 and hour >= 17:
        return False
    if weekday == 6 and hour < 18:
        return False
    # Daily metals break 17:00-18:00 New York.
    if hour == 17:
        return False
    return True


def _new_york():
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+

        return ZoneInfo("America/New_York")
    except ImportError:  # Python 3.7 in the bridge image
        import pytz  # noqa: WPS433  (dependency of pandas)

        return pytz.timezone("America/New_York")
