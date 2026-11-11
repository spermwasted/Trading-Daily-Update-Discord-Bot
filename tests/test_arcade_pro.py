import random
import time

import pytest
from discord.ext import commands


def drop(m, mask, stones, c):
    """Play column c for the side owning `stones`; returns (mask, stones)."""
    bit = (mask + m.gx_bottom(c)) & m.gx_colmask(c)
    return mask | bit, stones | bit


def test_bet_parsing(bot_mod):
    p = bot_mod.gx_parse_bet
    assert p("500", 1000) == 500 and p("half", 1000) == 500 and p("all", 1000) == 1000
    assert p("25%", 1000) == 250 and p("1k", 5000) == 1000
    for bad, bal in (("5", 100), ("abc", 100), ("2000", 100), ("-50", 100)):
        with pytest.raises(commands.BadArgument):
            p(bad, bal)


def test_crash_curve_and_house_edge(bot_mod):
    m = bot_mod
    assert m.gx_crash_point(0.0) == 1.0                         # inside the edge → instant crash
    assert m.gx_crash_point(0.5) == pytest.approx(1.94)
    assert m.gx_crash_point(0.999999) == m.GX_CRASH_CAP
    assert m.gx_crash_mult(0) == 1.0 and 1.95 < m.gx_crash_mult(8.2) < 2.05
    # P(crash ≥ 2x) should be ≈ (1 - edge) / 2 = 0.485
    rng = random.Random(1)
    n = 40_000
    hits = sum(m.gx_crash_point(rng.random()) >= 2 for _ in range(n)) / n
    assert hits == pytest.approx(0.485, abs=0.01)


def test_towers_fair_minus_edge(bot_mod):
    m = bot_mod
    for mode, (doors, traps) in m.GX_TOWER_MODES.items():
        p_safe = (doors - traps) / doors
        assert m.gx_tower_step(mode) * p_safe == pytest.approx(m.GX_TOWER_EDGE)   # EV per floor = 0.97


def test_c4_win_detection_all_directions(bot_mod):
    m = bot_mod
    H1 = m.GX_H1
    bit = lambda c, r: 1 << (c * H1 + r)
    assert m.gx_won(bit(0, 0) | bit(0, 1) | bit(0, 2) | bit(0, 3))                 # vertical
    assert m.gx_won(bit(1, 0) | bit(2, 0) | bit(3, 0) | bit(4, 0))                 # horizontal
    assert m.gx_won(bit(0, 0) | bit(1, 1) | bit(2, 2) | bit(3, 3))                 # diagonal /
    assert m.gx_won(bit(0, 3) | bit(1, 2) | bit(2, 1) | bit(3, 0))                 # diagonal \
    assert not m.gx_won(bit(0, 5) | bit(1, 0) | bit(2, 0) | bit(3, 0))             # no wrap-around
    assert not m.gx_won(bit(0, 3) | bit(0, 4) | bit(0, 5) | bit(1, 0))             # column boundary


def test_c4_ai_takes_win_and_blocks(bot_mod):
    m = bot_mod
    # AI (bot) has three in column 2 → must play 2 to win
    mask = bot = 0
    hum = 0
    for c in (2, 5, 2, 5, 2, 6):
        if c == 2:
            mask, bot = drop(m, mask, bot, c)
        else:
            mask, hum = drop(m, mask, hum, c)
    col, _ = m.gx_best_move(bot, mask, 6, "normal")
    assert col == 2
    # human threatens 3-in-a-row on the bottom row (cols 1,2,3) → AI must block at 0 or 4
    mask = hum = bot = 0
    for c, who in ((1, "h"), (1, "b"), (2, "h"), (2, "b"), (3, "h")):
        if who == "h":
            mask, hum = drop(m, mask, hum, c)
        else:
            mask, bot = drop(m, mask, bot, c)
    col, _ = m.gx_best_move(mask ^ hum, mask, 5, "normal")
    assert col in (0, 4)


def test_c4_hard_respects_time_budget(bot_mod):
    t = time.perf_counter()
    col, depth = bot_mod.gx_best_move(0, 0, 0, "hard", budget=0.5)
    assert col in range(7) and depth >= 3
    assert time.perf_counter() - t < 1.5


def test_lights_solver(bot_mod):
    m = bot_mod
    rng = random.Random(3)
    for n in (3, 4, 5):
        for _ in range(15):
            state = 0
            for i in rng.sample(range(n * n), k=rng.randint(1, n * n)):
                state = m.gx_lights_press(state, n, i)
            sol = m.gx_lights_solve(state, n)
            assert sol is not None
            s = state
            for i in range(n * n):
                if sol >> i & 1:
                    s = m.gx_lights_press(s, n, i)
            assert s == 0
    # a single lit corner on 5×5 is the classic unsolvable case
    assert m.gx_lights_solve(1, 5) is None


def test_dice_ranking(bot_mod):
    r = bot_mod.gx_dice_rank
    order = [[1, 3, 5, 2, 6], [2, 2, 3, 4, 6], [3, 3, 5, 5, 1], [4, 4, 4, 1, 2], [2, 3, 4, 5, 6],
             [6, 6, 2, 2, 2], [5, 5, 5, 5, 1], [3, 3, 3, 3, 3]]
    ranks = [r(d) for d in order]
    assert ranks == sorted(ranks)
    assert r([6, 6, 1, 2, 3]) > r([5, 5, 6, 4, 3])                # higher pair wins
    assert r([2, 2, 2, 6, 6]) > r([1, 1, 1, 6, 6]) and r([1, 2, 3, 4, 5]) < r([2, 3, 4, 5, 6])
    holds = bot_mod.gx_bot_holds([4, 4, 1, 2, 6])
    assert holds == [True, True, False, False, False]
    assert bot_mod.gx_bot_holds([2, 3, 4, 5, 5]).count(True) == 4      # keeps the 4-run, rerolls the extra 5


def test_profile_score(bot_mod):
    s = {"crash": {"wins": 2, "plays": 5}, "wordle": {"wins": 1, "plays": 1}, "junk": 3}
    assert bot_mod.gx_totals(s) == (3, 6)
    assert bot_mod.gx_arcade_score(s) == 3 * 10 + 2 * 25 + 6


def test_new_commands_registered(bot_mod):
    for n in ("crash", "towers", "c4bot", "lights", "dicepoker", "gameprofile", "arcadelb"):
        assert bot_mod.bot.get_command(n) is not None, n
    sh = bot_mod.bot.get_command("shield")
    for n in ("speed", "swarm", "slownuke", "watchdog", "scan", "engine"):
        assert sh.get_command(n) is not None, n
        assert n in bot_mod._ANTINUKE_FORWARD
    assert "arcade_pro" in bot_mod.HELP_PAGES


def test_game_cards_respect_discord_limits(bot_mod):
    """discord.py validates row widths / component counts when items are added — build every card."""
    import asyncio
    import types
    m = bot_mod
    ctx = types.SimpleNamespace(author=types.SimpleNamespace(id=1), guild=types.SimpleNamespace(id=1))

    def count(view):
        return len(list(view.walk_children()))

    async def go():
        for n in (3, 4, 5):
            v = m._GXLightsView(ctx, n).render("x")
            assert count(v) <= 40
        assert count(m._GXCrashView(1, 100).render(1.5, "live")) <= 40
        for mode in m.GX_TOWER_MODES:
            assert count(m._GXTowerView(ctx, 100, mode).render()) <= 40
        assert count(m._GXC4View(ctx, "hard").render()) <= 40
        assert count(m._GXDiceView(ctx, 100).render()) <= 40
    asyncio.run(go())
