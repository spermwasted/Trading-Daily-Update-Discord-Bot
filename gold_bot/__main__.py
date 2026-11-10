"""Entry point.

    python -m gold_bot                 run the Discord bot (needs DISCORD_BOT_TOKEN)
    python -m gold_bot --dry-run       build both reports from live FXCM data and
                                       print the embeds as JSON (no Discord)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from gold_bot.analysis.sessions import SessionSchedule
from gold_bot.config import Settings
from gold_bot.data.bridge_client import BridgeClient
from gold_bot.data.provider import DataUnavailable
from gold_bot.storage import Storage


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("discord").setLevel(logging.INFO)


async def _dry_run(settings: Settings) -> int:
    from gold_bot.embeds import developing_embed, session_embed
    from gold_bot.reports import ReportBuilder
    from gold_bot.service import utc_now

    provider = BridgeClient(settings.fxcm_bridge_url, settings.bridge_timeout_seconds, settings.bridge_retries)
    storage = Storage(":memory:")
    schedule = SessionSchedule(settings.session_times(), settings.session_zone)
    builder = ReportBuilder(settings, provider, storage, schedule)
    now = utc_now()
    try:
        print("Bridge health:", json.dumps(await provider.health(), indent=2))
        daily = await builder.build_daily(now)
        print(json.dumps(developing_embed(daily, settings.display_zone).to_dict(), indent=2, ensure_ascii=False))
        occurrence = schedule.current(now) or schedule.latest_open(now)
        if occurrence:
            session = await builder.build_session(occurrence, now)
            print(json.dumps(session_embed(session, settings.display_zone, "TRACKER").to_dict(), indent=2, ensure_ascii=False))
    except DataUnavailable as exc:
        print(f"FXCM data unavailable: {exc}")
        return 1
    finally:
        await provider.aclose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GOLD FXCM Discord bot")
    parser.add_argument("--dry-run", action="store_true", help="print reports from live FXCM data, no Discord")
    args = parser.parse_args()

    settings = Settings()
    _setup_logging(settings.log_level)
    if args.dry_run:
        return asyncio.run(_dry_run(settings))

    token = settings.discord_bot_token.get_secret_value()
    if not token:
        logging.error("DISCORD_BOT_TOKEN is not set")
        return 2
    if not settings.discord_daily_channel_id:
        logging.error("DISCORD_DAILY_CHANNEL_ID is not set")
        return 2

    from gold_bot.bot import DiscordPublisher, GoldBot
    from gold_bot.service import ReportService

    bot = GoldBot(settings)
    provider = BridgeClient(settings.fxcm_bridge_url, settings.bridge_timeout_seconds, settings.bridge_retries)
    storage = Storage(settings.database_path)
    schedule = SessionSchedule(settings.session_times(), settings.session_zone)
    bot.attach_service(ReportService(settings, provider, storage, DiscordPublisher(bot), schedule))
    bot.run(token, log_handler=None, reconnect=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
