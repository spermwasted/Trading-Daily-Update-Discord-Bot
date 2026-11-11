"""Antinuke engine v3 — drives the REAL hot path with fake audit entries and a fake guild,
so punishment, swarm, slow-nuke, trust and de-dupe logic are exercised without Discord."""
import asyncio
import itertools
import time
import types
from datetime import datetime, timezone

import discord
import pytest

_ids = itertools.count(10_000)


class FakeGuild:
    def __init__(self, gid=555, owner=1):
        self.id, self.owner_id = gid, owner
        self.banned, self.kicked = [], []
        self.me = types.SimpleNamespace(roles=[], top_role=None)
        self.name = "test"

    async def ban(self, target, reason=None, delete_message_seconds=0):
        await asyncio.sleep(0.01)                   # pretend API round-trip
        self.banned.append(target.id)

    async def kick(self, target, reason=None):
        self.kicked.append(target.id)

    def get_role(self, rid):
        return None

    def get_member(self, uid):
        return None

    def get_channel(self, cid):
        return None


def entry(guild, uid, action, target_id=None):
    return types.SimpleNamespace(
        id=next(_ids), guild=guild, user_id=uid, action=action,
        target=types.SimpleNamespace(id=target_id or next(_ids)),
        before=types.SimpleNamespace(), after=types.SimpleNamespace(),
        created_at=datetime.now(timezone.utc))


@pytest.fixture
def engine(bot_mod, monkeypatch):
    m = bot_mod
    m.bot._connection.user = types.SimpleNamespace(id=999)        # bot.user
    calls = {"recover": [], "alert": 0}

    async def fake_recover(guild, items, cfg):
        calls["recover"].append(len(items))
        return {}

    async def fake_alert(*a, **k):
        calls["alert"] += 1

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(m, "_an_recover", fake_recover)
    monkeypatch.setattr(m, "_an_alert", fake_alert)
    monkeypatch.setattr(m, "_an_dm_owner", noop)
    monkeypatch.setattr(m, "_an_recovery_report", noop)
    monkeypatch.setattr(m, "_an3_save", lambda *a, **k: None)
    for d in (m._AN_WIN, m._AN_RECENT, m._AN_QUAR, m._AN_SEEN, m._AN_HEAT, m._AN3_SLOW, m._AN3_SWARM, m._AN3_CC):
        d.clear()
    g = FakeGuild()
    m.antinuke_config[str(g.id)] = {"enabled": True, "punishment": "ban", "whitelist": [7], "window_seconds": 8,
                                    "strict_mode": False, "limits": {"channel_delete": 3, "role_delete": 3,
                                                                     "channel_update": 50, "ban": 3},
                                    "heat_limit": 0, "swarm_limit": 12, "slow_limit": 0, "dm_owner": False}
    yield m, g, calls
    m.antinuke_config.pop(str(g.id), None)


def run(coro):
    return asyncio.run(coro)


A = discord.AuditLogAction


def test_rate_limit_trip_and_quarantine(engine):
    m, g, calls = engine

    async def go():
        for _ in range(3):
            await m.on_audit_log_entry_create(entry(g, 42, A.channel_delete))
        assert g.banned == [42]
        await m.on_audit_log_entry_create(entry(g, 42, A.channel_delete))   # leftover → reverted, not re-punished
        await asyncio.sleep(0)
        assert g.banned == [42]
    run(go())
    assert calls["recover"] and calls["alert"] == 1
    assert m._AN3_METRICS[-1]["key"] == "channel_delete" and m._AN3_METRICS[-1]["decide_us"] > 0


def test_owner_whitelist_trust_and_dedupe(engine):
    m, g, calls = engine
    m.antinuke_config[str(g.id)]["trust"] = {"43": ["channel_delete"]}
    m._AN3_CC.clear()

    async def go():
        for _ in range(5):
            await m.on_audit_log_entry_create(entry(g, 1, A.channel_delete))     # guild owner
            await m.on_audit_log_entry_create(entry(g, 43, A.channel_delete))    # trusted for this module
        e = entry(g, 44, A.channel_delete)
        for _ in range(5):
            await m.on_audit_log_entry_create(e)                               # same entry id → processed once
    run(go())
    assert g.banned == []


def test_critical_first_offence(engine):
    m, g, _ = engine
    run(m.on_audit_log_entry_create(entry(g, 50, A.bot_add, target_id=777)))
    assert 50 in g.banned or 50 in g.kicked
    assert 777 in g.kicked                                                      # the rogue bot is removed too


def test_swarm_punishes_every_attacker_not_bystanders(engine):
    m, g, _ = engine

    async def go():
        # two attackers, each under their own limit of 3 channel deletes (2 each = heat 6 each → 12 total)
        for uid in (60, 61):
            for _ in range(2):
                await m.on_audit_log_entry_create(entry(g, uid, A.channel_delete))
        await asyncio.sleep(0.1)
    m.antinuke_config[str(g.id)]["limits"]["channel_delete"] = 5
    m._AN3_CC.clear()
    # a bystander with one small edit in the same seconds
    run(m.on_audit_log_entry_create(entry(g, 62, A.channel_update)))
    run(go())
    assert sorted(g.banned) == [60, 61]


def test_slow_nuke(engine, monkeypatch):
    m, g, _ = engine
    cfg = m.antinuke_config[str(g.id)]
    cfg.update(slow_limit=9, swarm_limit=0)
    cfg["limits"]["channel_delete"] = 50
    m._AN3_CC.clear()
    clock = [time.time()]
    monkeypatch.setattr(m.time, "time", lambda: clock[0])

    async def go():
        for i in range(3):                       # 3 deletes, 60 s apart: never 2 inside the 8 s window
            await m.on_audit_log_entry_create(entry(g, 70, A.channel_delete))
            clock[0] += 60
    run(go())
    assert g.banned == [70]
    assert m._AN3_METRICS[-1]["key"] == "slow_nuke"




def test_compiled_config_invalidation(engine):
    m, g, _ = engine
    cfg = m.antinuke_config[str(g.id)]
    cc = m._an3_cc(g.id, cfg)
    assert 7 in cc["wl"] and isinstance(cc["wl"], frozenset)
    cfg["whitelist"].append(8)
    m.save_antinuke_config()                     # save → cache dropped immediately
    assert 8 in m._an3_cc(g.id, cfg)["wl"]


def test_swarm_check_pure(bot_mod):
    m = bot_mod
    m._AN3_SWARM.clear()
    now = time.time()
    assert m._an3_swarm_check(1, 10, 6, now, 8, 12) == []
    assert m._an3_swarm_check(1, 11, 1, now, 8, 12) == []          # tiny share, still under limit
    hot = m._an3_swarm_check(1, 12, 6, now, 8, 12)
    assert hot == [10, 12] or sorted(hot) == [10, 12]               # 11 (heat 1) not swept in
    m._AN3_SWARM.clear()


def test_bench_and_pct(bot_mod):
    per = bot_mod._an3_bench(2000)
    assert 0 < per < 1000
    assert bot_mod._an3_pct([5, 1, 3], 50) == 3


def test_background_writer_is_atomic(bot_mod, tmp_path):
    p = tmp_path / "x.json"
    bot_mod._an3_write_payload(str(p), '{"a": 1}')
    assert p.read_text() == '{"a": 1}'
    assert not (tmp_path / "x.json.tmp").exists()
