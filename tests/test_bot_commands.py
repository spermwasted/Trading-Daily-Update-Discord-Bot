"""The /gold slash commands, exercised with a simulated Discord interaction."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gold_bot.bot import GoldBot
from tests.helpers import dt
from tests.test_service import build, load_market


def make_bot(tmp_path, now):
    service, provider, publisher, clock = build(tmp_path, now)
    load_market(provider, now)
    bot = GoldBot(service.settings)
    bot.attach_service(service)
    return bot, provider


def interaction():
    return SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def command(bot, name):
    (group,) = [c for c in bot.tree.get_commands() if c.name == "gold"]
    return {c.name: c for c in group.commands}[name]


def test_command_tree_has_gold_group(tmp_path):
    bot, _ = make_bot(tmp_path, dt("2026-11-10T08:30:00Z"))
    (group,) = bot.tree.get_commands()
    assert group.name == "gold" and sorted(c.name for c in group.commands) == ["analysis", "session", "status"]


def test_analysis_command_sends_daily_embed_with_developing_indication(tmp_path):
    bot, _ = make_bot(tmp_path, dt("2026-11-10T08:30:00Z"))
    it = interaction()
    asyncio.run(command(bot, "analysis").callback(it))
    embed = it.followup.send.await_args.kwargs["embed"]
    assert embed.title.endswith("DAILY MARKET ANALYSIS")
    assert any("DEVELOPING" in f.name for f in embed.fields)


def test_session_command_during_and_outside_sessions(tmp_path):
    bot, _ = make_bot(tmp_path, dt("2026-11-10T08:30:00Z"))  # London
    it = interaction()
    asyncio.run(command(bot, "session").callback(it))
    assert it.followup.send.await_args.kwargs["embed"].title.endswith("LONDON SESSION TRACKER")

    bot2, _ = make_bot(tmp_path / "b", dt("2026-11-10T22:30:00Z"))  # 17:30 NY: between sessions
    it2 = interaction()
    asyncio.run(command(bot2, "session").callback(it2))
    text = it2.followup.send.await_args.args[0]
    assert "No trading session" in text and "Asian" in text and "PKT" in text


def test_commands_report_data_outage_instead_of_failing(tmp_path):
    bot, provider = make_bot(tmp_path, dt("2026-11-10T08:30:00Z"))
    provider.fail = True
    it = interaction()
    asyncio.run(command(bot, "analysis").callback(it))
    assert "data unavailable" in it.followup.send.await_args.args[0]
    it3 = interaction()
    asyncio.run(command(bot, "status").callback(it3))
    assert "not connected" in it3.response.send_message.await_args.args[0]
