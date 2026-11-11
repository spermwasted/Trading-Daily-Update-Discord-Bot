# Trading-Daily-Update-Discord-Bot

All-in-one Discord bot (`bot.py`) — moderation, antinuke, games, economy, media tools — now
**v6.1.0 "Turbo Shield" (build 49)** — antinuke engine v3, new arcade games, and the
trading / daily-market-update system from 6.0.0.

## Run
```bash
pip install -r requirements.txt
export MOGGED_BOT_TOKEN="your-token"     # or put the token alone in token.txt next to bot.py
python bot.py
```
Enable **Server Members** and **Message Content** intents in the Developer Portal. Default prefix: `,`

## 📈 Market Desk (new in 6.0.0)
| Command | What it does |
|---|---|
| `,market` (`mkt`) | Indices, crypto, macro & Fear/Greed snapshot |
| `,stock <sym>` (`q`) | Live quote — stocks, ETFs, indices, FX, futures, crypto |
| `,chart <sym> [range]` | Candles + SMA20/50 + Bollinger + volume + RSI, range buttons |
| `,compare <sym> <sym> … [range]` | Normalised performance race (+ volatility, max drawdown) |
| `,technicals <sym>` (`analyze`) | RSI, MACD, SMA/EMA, ATR, Stochastic, Bollinger → composite score |
| `,pivots <sym>` | Classic + Fibonacci pivot levels |
| `,movers` · `,feargreed` · `,marketclock` | Crypto movers, sentiment, exchange sessions |
| `,watchlist [add/remove/clear]` | Personal watchlist with heat-map card |
| `,palert <sym> above/below <price>` · `,palert <sym> move 5%` · `,palerts` | Price alerts, delivered by DM |
| `,pbuy` · `,psell` · `,portfolio` · `,ptrades` · `,pleaderboard` · `,paperreset` | $100k paper trading |
| `,positionsize` · `,riskreward` · `,pnl` · `,compound` | Trading calculators |
| `,marketdaily setup #ch 09:30 America/New_York` | Scheduled **daily market brief** (`time`, `symbols`, `role`, `weekdays`, `now`, `off`) |

Symbols: `AAPL`, `BTC`, `ETH`, `EURUSD`, `EUR/USD`, `GOLD`, `OIL`, `SPX`, `^GSPC`, `RELIANCE.NS` …
Ranges: `1d 5d 1mo 3mo 6mo ytd 1y 2y 5y max`. Data: Yahoo Finance chart API, CoinGecko,
alternative.me — no API keys. Not financial advice.

## ⚡ Antinuke engine v3 "Turbo" (new in 6.1.0)
* **Faster:** compiled per-server config (O(1) hot path, µs decisions) · recovery starts in parallel with
  the punishment · disk writes moved to a background writer · concurrent audit-log sweeps.
* **Watchdog:** if Discord's audit event for a channel/role delete, ban, webhook or server edit hasn't
  arrived after 1.2 s, the bot pulls the audit log itself.
* **Swarm detection:** several accounts attacking together are all banned at once (bystanders with a
  tiny share are left alone). **Slow-nuke:** 30-minute heat budget per account.
* `,antinuke speed` (p50/p95 latency + live benchmark) · `,antinuke scan` (risk grade + fix list) ·
  `,antinuke swarm <n|off>` · `,antinuke slownuke <n|off>` · `,antinuke watchdog on|off` · `,antinuke engine`

## 🚀 Arcade Pro (new in 6.1.0)
| Command | Game |
|---|---|
| `,crash <bet>` | Live multiplier rocket — cash out before it explodes (3 % house edge) |
| `,towers <bet> [easy\|normal\|hard]` | Climb 8 floors picking safe doors, cash out any floor |
| `,c4bot [easy\|normal\|hard]` | Four in a row vs a bitboard alpha-beta AI with iterative deepening |
| `,lights [3-5]` | Toggle puzzle; 🧠 hints come from a GF(2) linear-algebra solver |
| `,dicepoker <bet>` | 5 dice vs the bot, hold & reroll once, 2× payout |
| `,gameprofile [@user]` · `,arcadelb` | Cross-game profile and all-round leaderboard |

Bets accept `500`, `2k`, `half`, `all`, `25%`.

## 🏷️ Versioning
* `BOT_VERSION` / `BOT_BUILD` / `BOT_CHANGELOG` live at the top of `bot.py` — bump them on each release.
* `,version` · `,version history` · `,changelog [version|all]`
* After an upgrade the owner gets one DM with the release notes; downgrades are flagged.

## Tests
```bash
pip install pytest pytest-asyncio
python -m pytest
```
