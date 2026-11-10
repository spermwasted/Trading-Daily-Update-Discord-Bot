import asyncio
from datetime import timedelta

import pytest

from gold_bot.analysis.sessions import SessionSchedule
from gold_bot.config import Settings
from gold_bot.models import Price
from gold_bot.service import ReportService
from gold_bot.storage import Storage
from tests.helpers import FakeProvider, FakePublisher, c, dt


def make_settings(**overrides):
    base = dict(discord_daily_channel_id=111, discord_session_channel_id=222, fvg_timeframes="H1")
    base.update(overrides)
    return Settings(_env_file=None, **base)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def build(tmp_path, now, db_name="bot.sqlite3"):
    settings = make_settings(database_path=str(tmp_path / db_name))
    provider, publisher = FakeProvider(), FakePublisher()
    storage = Storage(settings.database_path)
    schedule = SessionSchedule(settings.session_times(), settings.session_zone)
    clock = Clock(now)
    service = ReportService(settings, provider, storage, publisher, schedule, clock=clock)
    return service, provider, publisher, clock


def load_market(provider, upto):
    """Two completed daily candles + a new forming one opening Mon 9 Nov 17:00 EST."""
    provider.candles["D1"] = [
        c(dt("2026-11-05T22:00:00Z"), 4000, 4050, 3990, 4040),  # Fri
        c(dt("2026-11-08T22:00:00Z"), 4040, 4100, 4030, 4090),  # Mon: closes above Fri high
        c(dt("2026-11-09T22:00:00Z"), 4091, 4095, 4085, 4092),  # Tue (forming)
    ]
    m5 = []
    t = dt("2026-11-09T00:00:00Z")  # Sun 19:00 EST (Asia)
    price = 4060.0
    while t <= upto:
        m5.append(c(t, price, price + 1, price - 1, price + 0.5))
        price += 0.1
        t += timedelta(minutes=5)
    provider.candles["m5"] = m5
    provider.candles["H1"] = [x for x in m5 if x.time.minute == 0]
    provider.price = Price("XAU/USD", 4092.0, 4092.4, upto, 2, 0.01)


def run(coro):
    return asyncio.run(coro)


def test_daily_report_sent_once_on_new_daily_candle(tmp_path):
    now = dt("2026-11-09T23:05:00Z")  # Mon 18:05 EST, new trading day open
    service, provider, publisher, _ = build(tmp_path, now)
    load_market(provider, now)
    assert run(service.check_daily(now)) == "daily:2026-11-10"
    assert run(service.check_daily(now)) is None  # duplicate prevented
    assert len(publisher.sent) == 1
    channel, embed = publisher.sent[0]
    assert channel == 111 and embed.title == "GOLD FXCM | DAILY MARKET ANALYSIS"
    text = str(embed.to_dict())
    assert "BULLISH" in text and "4,100.00" in text  # Mon high is the previous day high


def test_daily_report_not_resent_after_restart(tmp_path):
    now = dt("2026-11-09T23:05:00Z")
    service, provider, publisher, _ = build(tmp_path, now)
    load_market(provider, now)
    run(service.check_daily(now))
    service2, provider2, publisher2, _ = build(tmp_path, now)  # same DB file
    load_market(provider2, now)
    assert run(service2.check_daily(now)) is None and publisher2.sent == []


def test_no_daily_report_when_newest_candle_is_complete(tmp_path):
    now = dt("2026-11-07T12:00:00Z")  # Saturday
    service, provider, publisher, _ = build(tmp_path, now)
    provider.candles["D1"] = [c(dt("2026-11-05T22:00:00Z"), 4000, 4050, 3990, 4040)]
    assert run(service.check_daily(now)) is None and publisher.sent == []


def test_failed_send_is_retried_next_poll(tmp_path):
    now = dt("2026-11-09T23:05:00Z")
    service, provider, publisher, _ = build(tmp_path, now)
    load_market(provider, now)
    publisher.fail = True
    with pytest.raises(RuntimeError):
        run(service.check_daily(now))
    publisher.fail = False
    assert run(service.check_daily(now)) == "daily:2026-11-10"


def test_provider_failure_sends_nothing_and_recovers(tmp_path):
    now = dt("2026-11-09T23:05:00Z")
    service, provider, publisher, _ = build(tmp_path, now)
    load_market(provider, now)
    provider.fail = True
    run(service.tick())  # must not raise
    assert publisher.sent == []
    provider.fail = False
    run(service.tick())
    assert len(publisher.sent) == 1


@pytest.mark.parametrize(
    "now, name",
    [
        ("2026-11-10T00:01:00Z", "ASIAN"),  # Mon 19:01 EST
        ("2026-11-10T08:01:00Z", "LONDON"),  # Tue 03:01 EST
        ("2026-11-10T13:01:00Z", "NEW YORK"),  # Tue 08:01 EST
    ],
)
def test_each_session_open_triggers_one_report(tmp_path, now, name):
    now = dt(now)
    service, provider, publisher, _ = build(tmp_path, now)
    load_market(provider, now)
    key = run(service.check_session(now))
    assert key and key.startswith("session:")
    assert run(service.check_session(now + timedelta(minutes=1))) is None
    assert len(publisher.sent) == 1
    channel, embed = publisher.sent[0]
    assert channel == 222 and embed.title == f"GOLD FXCM | {name} SESSION OPEN"
    assert "PKT" in str(embed.to_dict())


def test_session_waits_for_first_fxcm_tick(tmp_path):
    now = dt("2026-11-10T08:00:30Z")
    service, provider, publisher, _ = build(tmp_path, now)
    load_market(provider, dt("2026-11-10T07:55:00Z"))  # no m5 candle in London yet
    assert run(service.check_session(now)) is None and publisher.sent == []


def test_session_report_skipped_outside_grace(tmp_path):
    now = dt("2026-11-10T10:30:00Z")  # 2.5h after London open
    service, provider, publisher, _ = build(tmp_path, now)
    load_market(provider, now)
    assert run(service.check_session(now)) is None and publisher.sent == []
    assert service.storage.report_status("session:london:2026-11-10T08:00Z") == "skipped"


def test_daily_and_session_reports_are_independent(tmp_path):
    now = dt("2026-11-10T00:01:00Z")  # Asia open, new day already open
    service, provider, publisher, _ = build(tmp_path, now)
    load_market(provider, now)
    run(service.tick())
    titles = sorted(e.title for _, e in publisher.sent)
    assert titles == ["GOLD FXCM | ASIAN SESSION OPEN", "GOLD FXCM | DAILY MARKET ANALYSIS"]


def test_session_tracking_persists_state(tmp_path):
    now = dt("2026-11-10T09:00:00Z")
    service, provider, publisher, _ = build(tmp_path, now)
    load_market(provider, now)
    state = run(service.track_session(now))
    assert state["session"] == "london" and state["high"] >= state["low"]
    assert state["swept_previous_high"] is True  # prices rise steadily in the fixture
    assert service.storage.load_session_state("london:2026-11-10T08:00Z")["open"] == state["open"]
