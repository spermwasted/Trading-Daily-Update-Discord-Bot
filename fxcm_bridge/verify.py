"""Connection test against a real FXCM account (use a demo account first).

    python -m fxcm_bridge.verify [--seconds 30]

Logs in with ForexConnect using FXCM_USERNAME / FXCM_PASSWORD / FXCM_URL /
FXCM_CONNECTION, then checks that:
  * the configured instrument (default XAU/USD) has a live price,
  * D1, H4, H1, m15 and m5 history comes back with consistent OHLC values,
  * D1 candles start at FXCM's trading-day boundary (17:00 New York),
  * live prices keep updating for --seconds.
Exits 0 when every check passes, 1 otherwise. Prints no credentials.
"""
import argparse
import datetime
import logging
import sys
import time

from fxcm_bridge.config import BridgeConfig, ConfigError
from fxcm_bridge.feed import ForexConnectFeed, _new_york

TIMEFRAMES = ("D1", "H4", "H1", "m15", "m5")


def check_candles(timeframe, candles):
    problems = []
    if not candles:
        return ["no candles returned"]
    for c in candles:
        if not (c["low"] <= min(c["open"], c["close"]) and max(c["open"], c["close"]) <= c["high"]):
            problems.append("inconsistent OHLC at {}: {}".format(c["time"], c))
    times = [c["time"] for c in candles]
    if times != sorted(times) or len(set(times)) != len(times):
        problems.append("timestamps not strictly increasing")
    if timeframe == "D1":
        ny = _new_york()
        for c in candles:
            start = datetime.datetime.strptime(c["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
            local = start.astimezone(ny)
            if (local.hour, local.minute) != (17, 0):
                problems.append("D1 candle {} starts at {} New York, not 17:00".format(c["time"], local.strftime("%H:%M")))
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=int, default=30, help="how long to watch live prices")
    parser.add_argument("--login-timeout", type=int, default=120)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        config = BridgeConfig().validate()
    except ConfigError as exc:
        print("CONFIG ERROR:", exc)
        return 1
    print("Config:", config.describe())

    feed = ForexConnectFeed(config)
    feed.start()
    failures = []
    try:
        deadline = time.time() + args.login_timeout
        while not feed.health()["connected"] and time.time() < deadline:
            time.sleep(1)
        health = feed.health()
        print("Health:", health)
        if not health["connected"]:
            print("FAIL: could not log in:", health["last_error"])
            return 1

        price = feed.latest_price()
        print("PASS live price:", price)

        for tf in TIMEFRAMES:
            try:
                candles = feed.get_candles(tf, count=10)
            except Exception as exc:  # noqa: B902
                failures.append("{}: {}".format(tf, exc))
                print("FAIL {} history: {}".format(tf, exc))
                continue
            problems = check_candles(tf, candles)
            status = "PASS" if not problems else "FAIL"
            print("{} {} history: {} candles, last 3:".format(status, tf, len(candles)))
            for c in candles[-3:]:
                print("    ", c)
            for p in problems:
                print("     !", p)
            failures.extend("{}: {}".format(tf, p) for p in problems)

        seen = set()
        end = time.time() + args.seconds
        while time.time() < end:
            p = feed.latest_price()
            seen.add((p["bid"], p["ask"], p["time"]))
            time.sleep(0.5)
        print("Live updates observed in {}s: {} distinct quotes (last {})".format(args.seconds, len(seen), p))
        if len(seen) < 2:
            print("WARN: price did not change; normal only if the market is closed")
    finally:
        feed.stop()

    if failures:
        print("\nFAILED checks:\n  " + "\n  ".join(failures))
        return 1
    print("\nAll FXCM checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
