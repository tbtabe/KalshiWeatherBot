# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
python main.py                  # scan all markets, prompt to trade
python main.py --best           # find the single highest-edge market
python main.py --review         # show P&L history from trades.db
python main.py --diagnose       # test Kalshi API connectivity per series
python main.py --size 0.5       # scale position sizes (0.5 = half, 2.0 = double)
```

There are no tests and no build step. Dependencies: `pip install -r requirements.txt`.

## Configuration

All secrets and tunable parameters are in `.env` (loaded by `config.py` via `python-dotenv`):

| Variable | Default | Purpose |
|---|---|---|
| `KALSHI_API_KEY_ID` | — | Kalshi API key ID |
| `KALSHI_PRIVATE_KEY_PATH` | — | Path to RSA private key PEM file |
| `ANTHROPIC_API_KEY` | — | Anthropic API key for Claude Opus |
| `EDGE_THRESHOLD` | `0.15` | Minimum edge to surface a recommendation |
| `MAX_CONTRACTS` | `25` | Hard cap on contracts per order |
| `MAX_TRADE_RISK_DOLLARS` | `100.0` | Max dollars at risk per trade |
| `DB_PATH` | `trades.db` | SQLite trade journal path |

## Architecture

The bot runs a linear pipeline per market:

```
Kalshi API → ticker parse → weather fetch → statistical prob → Claude Opus → trade prompt
```

### Module responsibilities

**`kalshi/`** — Kalshi REST API client
- `auth.py`: RSA-PSS signature generation. Every request is signed: `timestamp + METHOD + path` (query params excluded from signature).
- `client.py`: `_AuthenticatedSession` subclasses `requests.Session` and injects auth headers on every `.request()` call. The session is shared across `MarketsAPI`, `OrdersAPI`, `PortfolioAPI`.
- `markets.py`: `get_weather_markets()` handles pagination automatically.

**`weather/`** — Weather data, two sources merged
- `geocoding.py`: For all cities in `SERIES_MAP`, `geocode()` returns hardcoded `GeoLocation` from `_KALSHI_STATIONS` — the exact NOAA ASOS station coordinates Kalshi uses for settlement (from each market's `rules_primary` field). This bypasses the geocoding API entirely for known cities. Stations confirmed from live Kalshi market rules are marked `[CONFIRMED]`; others are marked `[ASSUMED]` and should be verified against `rules_primary` when those series go live. Adding a new city requires an entry in both `SERIES_MAP` (ticker_parser.py) and `_KALSHI_STATIONS` (geocoding.py).
- `nws.py`: US-only. Two-step: `/points/{lat},{lon}` → grid, then `/gridpoints/{office}/{x},{y}/forecast`. NWS is preferred for US locations.
- `openmeteo.py`: Global fallback (`api.open-meteo.com`). Returns WMO weather codes mapped to human-readable summaries.
- `__init__.py`: `WeatherFetcher.get_report()` merges both sources (NWS preferred, Open-Meteo fills gaps). Results are cached in `_report_cache` keyed by `(city.lower(), date, country_code)` — this is a process-level cache to avoid redundant calls when multiple markets share the same city and date.

**`analyzer/`** — Two-phase probability estimation
- `ticker_parser.py`: Parses Kalshi tickers like `KXHIGHNY-26FEB27-T45`. `SERIES_MAP` maps series tickers to geocodable city names. All markets currently use `"between"` condition (1°F bracket). Adding a new city requires adding it to `SERIES_MAP`.
- `probability.py`: Normal distribution estimate. Uses inter-source spread as σ if both NWS and Open-Meteo available (floor 2.5°F), otherwise falls back to historical NWS RMSE of 4°F. Returns 0.50 when no forecast data exists.
- `claude_analyst.py`: Calls `claude-opus-4-6` with `thinking: {type: "adaptive"}`. Returns JSON with `probability_estimate`, `confidence`, `reasoning`, `recommend_trade`. Falls back to statistical estimate on any API failure.
- `__init__.py`: `Analyzer.analyze_best()` scores ALL markets statistically first (pass 1), then calls Claude only on the winner (pass 2). `analyze_all()` calls Claude on every market exceeding `edge_threshold` statistically.

**`tracker.py`** — SQLite trade journal (`trades.db`)
- Records placed orders; resolves outcomes by polling `GET /markets/{ticker}` for `status == "settled"` and `yes_price == 100` (YES won) or `0` (NO won).

**`main.py`** — CLI entry point and display layer. All terminal formatting is here; the other modules have no print statements.

### Position sizing

¼-Kelly criterion: `wager = 0.25 × (edge / (1 - price)) × bankroll`, capped by `MAX_CONTRACTS` and `MAX_TRADE_RISK_DOLLARS`.

### Ticker format

`KXHIGH{CITY}-{YY}{MON}{DD}-{T|B}{TEMP}`

- `T` markets: 1°F bracket where ticker is the lower bound — T45 → [45.0, 46.0)°F. Kalshi's top bracket for a series displays as "X+1°F or above" but the math is identical.
- `B` markets: midpoint ± 0.5°F bracket — B76.5 → [76.0, 77.0)°F

### Known network issues

Cloudflare WARP (and other VPN clients that install Network Extensions) can block Python's socket connections to external APIs even when "disconnected". The WARP system daemon (`com.cloudflare.1dot1dot1dot1.macos.warp.daemon.plist`) must be fully stopped, not just the GUI. Use `sudo launchctl unload /Library/LaunchDaemons/com.cloudflare.1dot1dot1dot1.macos.warp.daemon.plist` or restart the machine.
