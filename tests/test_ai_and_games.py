import asyncio
import contextlib
import io
import types

import pytest
from PIL import Image


# ── fakes ─────────────────────────────────────────────────────────────────────
def resp(text="hi", stop="end_turn", urls=(), content=None):
    blocks = content if content is not None else [
        types.SimpleNamespace(type="thinking", thinking=""),
        types.SimpleNamespace(type="text", text=text, citations=[types.SimpleNamespace(url=u) for u in urls])]
    return types.SimpleNamespace(stop_reason=stop, content=blocks,
                                 usage=types.SimpleNamespace(input_tokens=10, output_tokens=5,
                                                             cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def ai(bot_mod, monkeypatch):
    m = bot_mod
    client = FakeClient([])
    monkeypatch.setattr(m, "_ai_client", lambda: client)
    monkeypatch.setattr(m, "_ai_save", lambda: None)
    m._AI_MEM.clear()
    m._AI_USER_TS.clear()
    return m, client


def fake_message(m, text="hello", uid=1, cid=10, gid=20, attachments=()):
    sent = []

    @contextlib.asynccontextmanager
    async def typing():
        yield

    async def reply(content, **kw):
        sent.append(content)
        return types.SimpleNamespace(id=len(sent) + 1000)

    async def send(content, **kw):
        sent.append(content)
        return types.SimpleNamespace(id=len(sent) + 2000)

    guild = types.SimpleNamespace(id=gid, name="Test Server")
    channel = types.SimpleNamespace(id=cid, typing=typing, send=send)
    author = types.SimpleNamespace(id=uid, display_name=f"user{uid}", bot=False)
    msg = types.SimpleNamespace(content=text, author=author, guild=guild, channel=channel, reference=None,
                                attachments=list(attachments), reply=reply, mentions=[], mention_everyone=False)
    return msg, sent


# ── AI: request shape, resume, refusal, memory ────────────────────────────────
def test_ai_request_shape_and_sources(ai):
    m, client = ai
    client.replies = [resp("Paris is the capital.", urls=["https://a.example", "https://a.example", "https://b.example"])]
    g = types.SimpleNamespace(id=1, name="g")
    text, sources = asyncio.run(m.ai_complete(guild=g, system="sys", messages=[{"role": "user", "content": "q"}],
                                              web=True, effort="low"))
    assert text == "Paris is the capital." and sources == ["https://a.example", "https://b.example"]
    kw = client.calls[0]
    assert kw["model"] == m.AI_MODEL == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"] == {"effort": "low"} and kw["cache_control"] == {"type": "ephemeral"}
    assert kw["tools"][0]["type"] == "web_search_20260209"
    assert "thinking" not in kw and "temperature" not in kw


def test_ai_pause_turn_resumes_without_extra_user_message(ai):
    m, client = ai
    paused = resp(stop="pause_turn", content=[types.SimpleNamespace(type="server_tool_use")])
    client.replies = [paused, resp("done")]
    text, _ = asyncio.run(m.ai_complete(guild=None, system="s", messages=[{"role": "user", "content": "q"}],
                                        web=True, effort="low"))
    assert text == "done"
    second = client.calls[1]["messages"]
    assert [x["role"] for x in second] == ["user", "assistant"] and second[1]["content"] is paused.content


def test_ai_refusal_and_empty(ai):
    m, client = ai
    client.replies = [resp("", stop="refusal")]
    with pytest.raises(m.AIError):
        asyncio.run(m.ai_complete(guild=None, system="s", messages=[{"role": "user", "content": "q"}], web=False, effort="low"))
    client.replies = [resp("")]
    with pytest.raises(m.AIError):
        asyncio.run(m.ai_complete(guild=None, system="s", messages=[{"role": "user", "content": "q"}], web=False, effort="low"))


def test_ai_api_errors_become_friendly(ai):
    m, client = ai
    import httpx2
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    client.replies = [m._anthropic.RateLimitError("rl", response=httpx2.Response(429, request=req), body=None)]
    with pytest.raises(m.AIError, match="rate-limited"):
        asyncio.run(m.ai_complete(guild=None, system="s", messages=[{"role": "user", "content": "q"}], web=False, effort="low"))


def test_ai_chat_memory_and_reply(ai, monkeypatch):
    m, client = ai
    client.replies = [resp("Nice to meet you, user1!"), resp("You said your name is Ali.")]
    msg, sent = fake_message(m, "my name is Ali")
    asyncio.run(m.ai_chat(msg, "my name is Ali"))
    m._AI_USER_TS.clear()
    msg2, sent2 = fake_message(m, "what's my name?")
    asyncio.run(m.ai_chat(msg2, "what's my name?"))
    hist = client.calls[1]["messages"]
    assert [x["role"] for x in hist] == ["user", "assistant", "user"]
    assert "my name is Ali" in hist[0]["content"] and hist[1]["content"] == "Nice to meet you, user1!"
    assert hist[2]["content"][-1]["text"].startswith("[user1]")
    assert sent2 == ["You said your name is Ali."]
    assert client.calls[1]["system"].startswith("You are") and "Test Server" in client.calls[1]["system"]


def test_ai_images_and_budget(ai):
    m, client = ai
    att = types.SimpleNamespace(content_type="image/png", size=1000, url="https://cdn.example/x.png")
    bad = types.SimpleNamespace(content_type="application/zip", size=10, url="https://cdn.example/x.zip")
    msg, _ = fake_message(m, attachments=[att, bad])
    blocks = m._ai_user_content(msg, "what is this")
    assert blocks[0] == {"type": "image", "source": {"type": "url", "url": "https://cdn.example/x.png"}}
    assert blocks[-1]["type"] == "text" and len(blocks) == 2
    g = m._ai_g(777)
    g["daily_limit"] = 1
    g["usage"][m._ai_today()] = {"req": 1, "in": 0, "out": 0}
    assert not m._ai_budget_ok(777)


def test_ai_helpers(bot_mod):
    m = bot_mod
    assert m.ai_history_for_api([{"role": "assistant", "content": "x"}, {"role": "user", "content": "y"}])[0]["role"] == "user"
    long = "line\n" * 900
    chunks = m.ai_chunks(long)
    assert all(len(c) <= 2000 for c in chunks) and "".join(chunks).count("line") == 900
    code = "intro\n```python\n" + "print(1)\n" * 400 + "```\noutro"
    for c in m.ai_chunks(code):
        assert c.count("```") % 2 == 0 and len(c) <= 2000
    skip = lambda c: m.ai_should_skip(c, ",", 42, lambda w: w in ("help", "ai"))
    assert skip(",ai hello") and skip("<@42> help") and skip("<@!42> ,ban x")
    assert not skip("<@42> how are you?") and not skip("hey there")


# ── game lookup parsing ───────────────────────────────────────────────────────
def test_riot_id(bot_mod):
    assert bot_mod.gl_parse_riot_id("Ten Z#0505") == ("Ten Z", "0505")
    for bad in ("TenZ", "a#1", "x" * 20 + "#123"):
        with pytest.raises(bot_mod.GameLookupError):
            bot_mod.gl_parse_riot_id(bad)


def test_val_mmr_both_shapes(bot_mod):
    v2 = {"data": {"current_data": {"currenttier": 21, "currenttierpatched": "Ascendant 1", "ranking_in_tier": 45,
                                    "mmr_change_to_last_game": -12, "elo": 1845},
                   "highest_rank": {"patched_tier": "Immortal 1", "season": "e8a2"}}}
    v3 = {"data": {"current": {"tier": {"id": 21, "name": "Ascendant 1"}, "rr": 45, "last_change": -12, "elo": 1845},
                   "peak": {"tier": {"name": "Immortal 1"}, "season": {"short": "e8a2"}}}}
    a, b = bot_mod.gl_val_mmr(v2), bot_mod.gl_val_mmr(v3)
    assert a == b and a["tier_name"] == "Ascendant 1" and a["change"] == -12 and a["peak"] == "Immortal 1"


def test_val_matches(bot_mod):
    js = {"data": [{"metadata": {"map": "Ascent", "mode": "Competitive"},
                    "players": {"all_players": [
                        {"name": "Other", "tag": "1", "team": "Red", "stats": {}},
                        {"name": "TenZ", "tag": "0505", "team": "Blue", "character": "Jett",
                         "stats": {"kills": 25, "deaths": 12, "assists": 4, "headshots": 30, "bodyshots": 60, "legshots": 10}}]},
                    "teams": {"red": {"has_won": False, "rounds_won": 9}, "blue": {"has_won": True, "rounds_won": 13}}}]}
    (mt,) = bot_mod.gl_val_matches(js, "tenz", "0505")
    assert mt["won"] is True and mt["agent"] == "Jett" and mt["hs"] == pytest.approx(30) and mt["k"] == 25


def test_self_card_fields(bot_mod):
    f = bot_mod.gl_parse_fields('ign=Shadow id=5123 rank="Ace Master" kd=4.2', bot_mod.GL_SELF_GAMES["bgmi"]["fields"])
    assert f == {"ign": "Shadow", "id": "5123", "rank": "Ace Master", "kd": "4.2"}
    for bad in ("nonsense", "hack=1", 'ign="open'):
        with pytest.raises(bot_mod.GameLookupError):
            bot_mod.gl_parse_fields(bad, bot_mod.GL_SELF_GAMES["bgmi"]["fields"])
    assert "@​everyone" in bot_mod.gl_parse_fields("ign=@everyone", ["ign"])["ign"]


def test_instagram_parse(bot_mod):
    js = {"data": {"user": {"username": "nasa", "full_name": "NASA", "biography": "Explore the universe",
                            "edge_followed_by": {"count": 100_000}, "edge_follow": {"count": 80},
                            "is_verified": True, "is_private": False, "category_name": "Government organization",
                            "edge_owner_to_timeline_media": {"count": 4000, "edges": [
                                {"node": {"thumbnail_src": "t1", "edge_liked_by": {"count": 900},
                                          "edge_media_to_comment": {"count": 100}, "taken_at_timestamp": 1_700_172_800}},
                                {"node": {"thumbnail_src": "t2", "edge_liked_by": {"count": 1900},
                                          "edge_media_to_comment": {"count": 100}, "taken_at_timestamp": 1_700_000_000}}]}}}}
    p = bot_mod.ig_parse_profile(js)
    assert p["verified"] and p["followers"] == 100_000 and p["posts"] == 4000
    assert p["engagement"] == pytest.approx(1.5)              # (1000 + 2000) / 2 / 100k
    assert p["days_between"] == pytest.approx(2.0)
    assert bot_mod._ig_short(1_234_567) == "1.2M" and bot_mod._ig_short(950) == "950"
    with pytest.raises(bot_mod.GameLookupError):
        bot_mod.ig_parse_profile({"data": {"user": None}})


# ── renderer ──────────────────────────────────────────────────────────────────
def _png(color=(200, 50, 50), size=(300, 200)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def test_card_renderer(bot_mod):
    r = bot_mod.gl_render_card
    full = r(title="TenZ#0505", subtitle="Valorant · NA", avatar=_png(), banner=_png(size=(900, 300)), badge_icon=_png(size=(64, 64)),
             badge="Immortal 3", badge_sub="245 RR", verified=True, bio="word " * 120,
             stats=[("Rank", "Immortal 3"), ("RR", "245"), ("Peak", "Radiant with a very long name indeed"), ("Level", "512"),
                    ("K/D", "1.42"), ("Win", "3/5"), ("HS", "31%"), ("Region", "NA"), ("ignored", "9th")],
             rows=[((38, 214, 140), "Ascent · Jett · 25/12/4")] * 7, thumbs=[_png(), b"not an image", _png()],
             brand="test")
    im = Image.open(io.BytesIO(full))
    assert im.size[0] == 1100 and im.size[1] > 600
    bare = Image.open(io.BytesIO(r(title="x")))                         # no images at all → placeholder avatar
    assert bare.size[0] == 1100


def test_new_commands_registered(bot_mod):
    for n in ("ai", "summarize", "explain", "valorant", "valagent", "fortnite", "steam", "chess", "roblox", "genshin",
              "freegames", "bgmi", "cod", "freefire", "gamercard", "gamenews", "igprofile"):
        assert bot_mod.bot.get_command(n) is not None, n
    assert bot_mod.bot.get_command("bgmi set") is not None and bot_mod.bot.get_command("ai channel") is not None
    prof = bot_mod.bot.get_command("instagram profile")
    assert prof.callback.__name__ == "cmd_instagram_profile_v2"
    assert bot_mod.BOT_CHANGELOG[0]["version"] == bot_mod.BOT_VERSION == "6.2.0"


def test_card_text_cleaning(bot_mod):
    assert bot_mod.gl_clean("🚀 Exploring ✨ space 👨‍🚀!") == "Exploring space !"
    assert bot_mod.gl_clean("Ünïcode ok — 漢字") == "Ünïcode ok — 漢字"
