# Kalshi Weather Bot

**Built an automated trading bot that scans Kalshi weather markets, merges NWS and Open-Meteo forecasts into a statistical edge, and uses Claude Opus to size and place ¼-Kelly trades.**

Scans Kalshi temperature markets, fetches forecasts from NWS (US) and Open-Meteo (global fallback), estimates probabilities, and surfaces trade opportunities above a configurable edge threshold—with optional interactive order placement.

## Quick start

```bash
pip install -r requirements.txt
# Create .env with your API keys (see Configuration below)
python main.py         # scan markets, prompt to trade
```

### Commands

| Command | Description |
|--------|-------------|
| `python main.py` | Scan all markets, prompt to trade |
| `python main.py --best` | Find the single highest-edge market |
| `python main.py --review` | Show P&L history from `trades.db` |
| `python main.py --diagnose` | Test Kalshi API connectivity per series |
| `python main.py --size 0.5` | Scale position sizes (0.5 = half, 2.0 = double) |

## Configuration

Secrets and tunables live in `.env` (see `config.py`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `KALSHI_API_KEY_ID` | — | Kalshi API key ID |
| `KALSHI_PRIVATE_KEY_PATH` | — | Path to RSA private key PEM |
| `ANTHROPIC_API_KEY` | — | Anthropic API key for Claude Opus |
| `EDGE_THRESHOLD` | `0.15` | Minimum edge to surface a recommendation |
| `MAX_CONTRACTS` | `25` | Hard cap on contracts per order |
| `MAX_TRADE_RISK_DOLLARS` | `100.0` | Max dollars at risk per trade |
| `DB_PATH` | `trades.db` | SQLite trade journal path |

## Architecture

```
Kalshi API → ticker parse → weather fetch → statistical prob → Claude Opus → trade prompt
```

- **kalshi/** — REST client (auth, markets, orders, portfolio)
- **weather/** — NWS + Open-Meteo merge; geocoding aligned to Kalshi's NOAA ASOS stations
- **analyzer/** — Ticker parsing, normal-distribution probability, Claude Opus analyst
- **tracker.py** — SQLite trade journal; resolves outcomes when markets settle
- **main.py** — CLI and display

Position sizing: ¼-Kelly, capped by `MAX_CONTRACTS` and `MAX_TRADE_RISK_DOLLARS`.

## License

MIT
