# Trading-Daily-Update-Discord-Bot

All-in-one Discord bot (`bot.py`) — moderation, antinuke, games, economy, media tools — now
**v6.0.0 "Market Desk" (build 48)** with a full trading / daily-market-update system.

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

## 🏷️ Versioning
* `BOT_VERSION` / `BOT_BUILD` / `BOT_CHANGELOG` live at the top of `bot.py` — bump them on each release.
* `,version` · `,version history` · `,changelog [version|all]`
* After an upgrade the owner gets one DM with the release notes; downgrades are flagged.

## Tests
```bash
pip install pytest pytest-asyncio
python -m pytest
```
