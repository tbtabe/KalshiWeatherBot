"""
Kalshi Weather Trading Bot — Interactive Main Loop

Scans all known Kalshi weather market series, fetches forecasts from NWS and
Open-Meteo, runs Claude Opus analysis, and presents trade opportunities above
the configured edge threshold for your y/n approval before placing orders.

Usage:
  python main.py                   — scan markets and trade interactively
  python main.py --size 0.5        — trade at half the bot's suggested size
  python main.py --size 2.0        — trade at double the bot's suggested size
  python main.py --review          — show P&L history from the local trade journal
"""

from __future__ import annotations

import argparse
import logging
import sys
import textwrap

from config import (
    ANTHROPIC_API_KEY,
    DB_PATH,
    EDGE_THRESHOLD,
    MAX_CONTRACTS,
    MAX_TRADE_RISK_DOLLARS,
)
from analyzer import Analyzer, TradeRecommendation
from kalshi import KalshiClient
from tracker import TradeTracker

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ── Terminal display helpers ──────────────────────────────────────────────────

_W = 64  # display width


def _hr(char: str = "━") -> None:
    print(char * _W)


def _section(title: str) -> None:
    _hr()
    print(f"  {title}")
    _hr()


def _wrap(text: str, indent: int = 4) -> str:
    """Word-wrap text at display width with consistent indentation."""
    prefix = " " * indent
    return textwrap.fill(
        text, width=_W - indent, initial_indent=prefix, subsequent_indent=prefix
    )


def _prompt_choice(question: str) -> str:
    """Prompt for y / n / s (skip remaining). Returns 'y', 'n', or 's'."""
    while True:
        try:
            raw = input(
                f"\n  {question}  [y]es / [n]o / [s]kip remaining:  "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  Interrupted — exiting.")
            return "s"
        if raw in ("y", "yes"):
            return "y"
        if raw in ("n", "no"):
            return "n"
        if raw in ("s", "skip"):
            return "s"
        print("  Please enter y, n, or s.")


# ── Recommendation display ────────────────────────────────────────────────────

def _apply_size(
    rec: TradeRecommendation, size: float, balance_cap: float | None = None
) -> tuple[int, float, float, float]:
    """
    Scale rec's suggested_contracts by *size* and recompute financials.

    If balance_cap is given, contracts are further limited so that risk_dollars
    does not exceed the available balance.

    Returns (contracts, risk_dollars, max_gain_dollars, expected_value_dollars).
    Contracts are at least 1 unless balance_cap is too small, in which case 0.
    """
    contracts = max(1, round(rec.suggested_contracts * size))
    p    = rec.market_price_cents / 100
    if balance_cap is not None and p > 0:
        contracts = min(contracts, int(balance_cap / p))
    p_win = rec.claude_probability if rec.side == "yes" else (1 - rec.claude_probability)
    risk     = contracts * p
    max_gain = contracts * (1 - p)
    ev       = contracts * (p_win * (1 - p) - (1 - p_win) * p)
    return contracts, risk, max_gain, ev


def _kalshi_slug(series: str, city: str) -> str:
    verb = "lowest" if series.startswith("KXLOWT") else "highest"
    city_name = city.split(",")[0].lower().replace(".", "").replace(" ", "-")
    return f"{verb}-temperature-in-{city_name}"


def _kalshi_url(rec: TradeRecommendation) -> str:
    ticker = rec.spec.ticker
    event_ticker = rec.market.get("event_ticker") or ticker.rsplit("-", 1)[0]
    slug = _kalshi_slug(rec.spec.series, rec.spec.city)
    return (
        f"https://kalshi.com/markets/{rec.spec.series.lower()}"
        f"/{slug}/{event_ticker.lower()}"
    )


def _show_recommendation(
    rec: TradeRecommendation, index: int, total: int, size: float = 1.0,
    balance_cap: float | None = None,
) -> tuple[int, float, float, float]:
    """
    Render a single trade recommendation to the terminal.

    Returns the (possibly size-adjusted and balance-capped) tuple so the caller
    can use it when placing the order without recomputing.
    """
    contracts, risk, max_gain, ev = _apply_size(rec, size, balance_cap)

    print()
    side = rec.side.upper()
    var_label = "low" if rec.spec.variable == "low" else "high"
    if rec.spec.condition == "between" and rec.spec.upper_bound_f is not None:
        cond = (
            f"{rec.spec.display_name} {var_label} between "
            f"{rec.spec.threshold_f:.1f}–{rec.spec.upper_bound_f:.1f}°F "
            f"on {rec.spec.date}"
        )
    else:
        cond = (
            f"{rec.spec.display_name} {var_label} {rec.spec.condition} "
            f"{rec.spec.threshold_f:.0f}°F on {rec.spec.date}"
        )
    _section(f"[{index}/{total}]  {rec.spec.ticker}  —  {cond}")
    print(f"  {_kalshi_url(rec)}")

    # Prices and edge
    print(
        f"  Market {side} price:    {rec.market_price_cents}¢  "
        f"({rec.market_price_cents}% market-implied)"
    )
    print(
        f"  Our estimate:       {rec.stat_probability:.0%}  "
        f"(NWS + Open-Meteo, normal dist)"
    )
    print(
        f"  Claude estimate:    {rec.claude_probability:.0%}  "
        f"[{rec.confidence.upper()} confidence]"
    )
    print(f"  Edge:              +{rec.edge * 100:.0f}%  →  BUY {side}")
    print()

    # Forecast table + source links
    lat, lon = rec.weather.latitude, rec.weather.longitude
    nws_url = (
        f"https://forecast.weather.gov/MapClick.php?lat={lat:.4f}&lon={lon:.4f}"
    )
    om_url = (
        f"https://open-meteo.com/en/docs"
        f"#latitude={lat:.4f}&longitude={lon:.4f}"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        f"&temperature_unit=fahrenheit"
    )
    source_urls = {"nws": nws_url, "open_meteo": om_url}

    print("  Forecast:")
    for fc in rec.weather.forecasts:
        high = f"{fc.temp_high_f:.0f}°F" if fc.temp_high_f is not None else "N/A"
        low  = f"{fc.temp_low_f:.0f}°F"  if fc.temp_low_f  is not None else "N/A"
        prec = f"{fc.precip_probability:.0f}%" if fc.precip_probability is not None else "N/A"
        src  = "NWS       " if fc.source == "nws" else "Open-Meteo"
        summ = f'  "{fc.summary}"' if fc.summary else ""
        print(f"    {src}  {high} / {low}  |  precip {prec}{summ}")
        url = source_urls.get(fc.source)
        if url:
            print(f"    {' ' * 10}  {url}")

    # Active alerts
    if rec.weather.alerts:
        print(f"  Alerts ({len(rec.weather.alerts)}):")
        for a in rec.weather.alerts:
            print(f"    [{a.severity}] {a.event}: {a.headline}")
    else:
        print("  Alerts:     None")
    print()

    # Claude's reasoning
    print("  Analysis:")
    print(_wrap(rec.reasoning))
    print()

    # Trade details — show size / balance-cap notes
    uncapped = max(1, round(rec.suggested_contracts * size))
    notes = []
    if size != 1.0:
        notes.append(f"size ×{size:.2g}")
    if balance_cap is not None and contracts < uncapped:
        notes.append(f"balance-capped from {uncapped}")
    note_str = f"  ({', '.join(notes)})" if notes else ""
    print(
        f"  Suggested trade:  BUY {contracts}x {side} "
        f"@ {rec.market_price_cents}¢{note_str}"
    )
    print(
        f"    Risk: ${risk:.2f}  |  "
        f"Max gain: ${max_gain:.2f}  |  "
        f"Expected value: +${ev:.2f}"
    )
    _hr("─")

    return contracts, risk, max_gain, ev


# ── Portfolio summary ─────────────────────────────────────────────────────────

def _show_summary(
    executed: list[tuple[TradeRecommendation, dict, int]],
    client: KalshiClient,
) -> None:
    """Print the post-session summary: orders placed + current portfolio."""
    print()
    _section("SESSION SUMMARY")

    if executed:
        print(f"  Orders placed: {len(executed)}")
        for rec, order, contracts in executed:
            oid = order.get("order", {}).get("order_id", "")
            oid_str = f"  id={oid}" if oid else ""
            print(
                f"    {rec.spec.ticker:44s}  "
                f"BUY {contracts}x {rec.side.upper()} "
                f"@ {rec.market_price_cents}¢{oid_str}"
            )
    else:
        print("  No orders placed this session.")

    print()
    print("  Portfolio:")

    try:
        balance = client.portfolio.get_balance()
        print(f"    Cash balance:  ${balance:,.2f}")
    except Exception as exc:
        print(f"    Cash balance:  unavailable  ({exc})")

    try:
        positions = client.portfolio.get_all_positions()
        if positions:
            print(f"    Open positions ({len(positions)}):")
            for pos in positions[:30]:
                ticker    = pos.get("ticker", "?")
                contracts = pos.get("position", 0)
                raw_val   = pos.get("value", 0)
                value     = (
                    raw_val / 100 if isinstance(raw_val, int) else float(raw_val or 0)
                )
                direction = "YES" if contracts >= 0 else "NO"
                print(
                    f"      {ticker:44s}  {direction} x{abs(contracts):<3d}  "
                    f"value ${value:6.2f}"
                )
            if len(positions) > 30:
                print(f"      … and {len(positions) - 30} more")
        else:
            print("    Open positions: none")
    except Exception as exc:
        print(f"    Open positions: unavailable  ({exc})")

    _hr()


# ── Review mode ───────────────────────────────────────────────────────────────

def review_mode(tracker: TradeTracker, client: KalshiClient) -> None:
    """Display the full P&L history from the local trade journal."""
    _section("KALSHI WEATHER TRADING BOT  —  P&L REVIEW")

    # Try to resolve any pending trades first
    print("  Checking for newly settled markets…")
    try:
        resolved = tracker.resolve_pending(client)
        if resolved:
            print(f"  Resolved {resolved} trade{'s' if resolved != 1 else ''}.\n")
        else:
            print("  No new resolutions.\n")
    except Exception as exc:
        print(f"  Could not resolve pending trades: {exc}\n")

    summary = tracker.get_summary()

    # ── Aggregate stats ──
    total    = summary["total_trades"]
    wins     = summary["wins"]
    losses   = summary["losses"]
    pending  = summary["pending"]
    settled  = wins + losses
    win_rate = summary["win_rate"]
    pnl      = summary["total_pnl"]
    roi      = summary["roi"]

    if total == 0:
        print("  No trades recorded yet.")
        _hr()
        return

    print(f"  Trades:   {total} total  ({settled} settled, {pending} pending)")
    if settled:
        wr_str  = f"{win_rate:.0%}" if win_rate is not None else "—"
        pnl_str = f"${pnl:+.2f}" if pnl is not None else "—"
        roi_str = f"{roi:.1%}" if roi is not None else "—"
        print(f"  Win rate: {wr_str}  ({wins}W / {losses}L)")
        print(f"  P&L:      {pnl_str}  |  ROI: {roi_str}")

        # ── By-confidence breakdown ──
        by_conf = summary["by_confidence"]
        if by_conf:
            print()
            print("  By confidence level:")
            for conf in ("high", "medium", "low", "unknown"):
                if conf not in by_conf:
                    continue
                b = by_conf[conf]
                conf_settled = b["wins"] + b["losses"]
                conf_wr = b["wins"] / conf_settled if conf_settled else None
                wr_s = f"{conf_wr:.0%}" if conf_wr is not None else "—"
                print(
                    f"    {conf.upper():<8}  {b['wins']}W / {b['losses']}L  "
                    f"({wr_s})  P&L ${b['pnl']:+.2f}"
                )

    # ── Trade history table ──
    print()
    _hr("─")
    print(
        f"  {'Date':<12} {'Ticker':<36} {'Side':<4} {'Ct':>3} "
        f"{'Edge':>5} {'Conf':<6} {'Outcome':<8} {'P&L':>7}"
    )
    _hr("─")

    all_trades = tracker.get_trades()
    for row in all_trades:
        date_str = (row["placed_at"] or "")[:10]
        ticker   = (row["ticker"] or "")[:35]
        side     = (row["side"] or "").upper()[:3]
        ct       = row["contracts"] or 0
        edge     = f"{(row['edge'] or 0)*100:.0f}%"
        conf     = (row["confidence"] or "")[:6].upper()
        outcome  = row["outcome"] or "pending"

        if outcome == "win":
            pnl_s = f"+${row['pnl_dollars']:.2f}"
        elif outcome == "loss":
            pnl_s = f"-${abs(row['pnl_dollars']):.2f}"
        else:
            cost = row["cost_dollars"] or 0
            pnl_s = f"({cost:.2f})"  # show cost-at-risk for pending

        print(
            f"  {date_str:<12} {ticker:<36} {side:<4} {ct:>3} "
            f"{edge:>5} {conf:<6} {outcome:<8} {pnl_s:>7}"
        )

    _hr("─")

    # ── Pending trades ──
    pending_rows = tracker.get_trades(outcome="pending")
    if pending_rows:
        print(f"\n  {len(pending_rows)} pending trade(s) — awaiting market settlement.")

    _hr()


# ── Main entry point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kalshi Weather Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Show P&L history from the local trade journal and exit.",
    )
    parser.add_argument(
        "--size",
        type=float,
        default=1.0,
        metavar="MULTIPLIER",
        help=(
            "Scale every suggested position by this factor. "
            "0.5 = half size, 2.0 = double size. Default: 1.0"
        ),
    )
    parser.add_argument(
        "--best",
        action="store_true",
        help=(
            "Find and display the single weather market with the highest edge, "
            "even if it falls below the normal edge threshold. "
            "Prompts to trade if desired."
        ),
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help=(
            "Query every known weather series and report how many open markets "
            "exist for each. Use this to verify connectivity and credentials."
        ),
    )
    args = parser.parse_args()

    if args.size <= 0:
        print("  --size must be a positive number.")
        sys.exit(1)

    tracker = TradeTracker(DB_PATH)

    # ── Kalshi client ─────────────────────────────────────────────────────
    try:
        client = KalshiClient.from_config()
    except Exception as exc:
        print(f"  Failed to initialize Kalshi client: {exc}")
        sys.exit(1)

    # ── Review mode ───────────────────────────────────────────────────────
    if args.review:
        review_mode(tracker, client)
        return

    # ── Diagnose mode ─────────────────────────────────────────────────────
    if args.diagnose:
        from analyzer.ticker_parser import SERIES_MAP
        _section("KALSHI WEATHER TRADING BOT  —  DIAGNOSE")

        total = 0
        errors = 0
        print(f"  Querying {len(SERIES_MAP)} known series...\n")
        for series in sorted(SERIES_MAP):
            try:
                resp = client.markets.get_markets(
                    status="open", limit=10, series_ticker=series
                )
                mkts = resp.get("markets", [])
                if mkts:
                    total += len(mkts)
                    example = mkts[0].get("ticker", "")
                    print(f"  {series:<16} {len(mkts):>2} market(s)  e.g. {example}")
            except Exception as exc:
                errors += 1
                print(f"  {series:<16} ERROR: {exc}")

        print(f"\n  Total: {total} open markets across all series")
        if errors:
            print(f"  {errors} series returned errors — check connectivity or credentials")
        _hr()
        return

    # ── Best-market mode ───────────────────────────────────────────────────
    if args.best:
        _section("KALSHI WEATHER TRADING BOT  —  BEST MARKET")

        bankroll = 1_000.0
        try:
            bankroll = client.portfolio.get_balance()
            print(f"  Account balance:  ${bankroll:,.2f}")
        except Exception as exc:
            print(f"  Balance unavailable (using ${bankroll:.0f} default): {exc}")
        print()

        analyzer = Analyzer(
            api_key=ANTHROPIC_API_KEY,
            bankroll=bankroll,
            max_contracts=MAX_CONTRACTS,
            max_risk_dollars=MAX_TRADE_RISK_DOLLARS,
            edge_threshold=EDGE_THRESHOLD,
        )

        print("  Scanning weather markets across all known series...")
        try:
            all_markets = analyzer.fetch_all_markets(client)
        except RuntimeError as exc:
            print(f"\n  ERROR: {exc}")
            sys.exit(1)

        total_markets = sum(len(v) for v in all_markets.values())
        print(f"  {total_markets} markets found. Running analysis to find best edge...\n")

        rec = analyzer.analyze_best(all_markets)

        if rec is None:
            print("  No analyzable markets found.")
            _hr()
            return

        contracts, risk, max_gain, ev = _show_recommendation(rec, 1, 1, args.size, balance_cap=bankroll)

        if contracts == 0:
            price = rec.market_price_cents / 100
            print(f"  Cannot place trade: balance ${bankroll:.2f} is less than ${price:.2f}/contract.")
            _show_summary([], client)
            return

        edge_note = (
            f"+{rec.edge * 100:.1f}%  ✓ above threshold"
            if rec.edge >= EDGE_THRESHOLD
            else f"{rec.edge * 100:.1f}%  (below {EDGE_THRESHOLD:.0%} threshold)"
        )
        print(f"  Best edge available: {edge_note}")

        choice = _prompt_choice("Place this trade?")
        executed: list[tuple[TradeRecommendation, dict, int]] = []

        if choice == "y":
            try:
                order = client.orders.place_order(
                    ticker=rec.spec.ticker,
                    side=rec.side,
                    action="buy",
                    order_type="limit",
                    count=contracts,
                    yes_price=rec.market_price_cents if rec.side == "yes" else None,
                    no_price=rec.market_price_cents if rec.side == "no" else None,
                )
                executed.append((rec, order, contracts))
                tracker.record_trade(rec, order)
                print(
                    f"\n  Placed: {contracts}x {rec.side.upper()} "
                    f"on {rec.spec.ticker} @ {rec.market_price_cents}¢"
                )
            except Exception as exc:
                print(f"\n  Order failed: {exc}")

        _show_summary(executed, client)
        return

    # ── Trading mode ──────────────────────────────────────────────────────
    _section("KALSHI WEATHER TRADING BOT")

    bankroll = 1_000.0
    try:
        bankroll = client.portfolio.get_balance()
        print(f"  Account balance:  ${bankroll:,.2f}")
    except Exception as exc:
        print(f"  Balance unavailable (using ${bankroll:.0f} default): {exc}")
    print()

    # ── Analyzer ──────────────────────────────────────────────────────────
    analyzer = Analyzer(
        api_key=ANTHROPIC_API_KEY,
        bankroll=bankroll,
        max_contracts=MAX_CONTRACTS,
        max_risk_dollars=MAX_TRADE_RISK_DOLLARS,
        edge_threshold=EDGE_THRESHOLD,
    )

    # ── Scan markets ──────────────────────────────────────────────────────
    print("  Scanning weather markets across all known series...")
    try:
        all_markets = analyzer.fetch_all_markets(client)
    except RuntimeError as exc:
        print(f"\n  ERROR: {exc}")
        print("  Run  python main.py --diagnose  to inspect raw API responses.")
        sys.exit(1)

    total_markets = sum(len(v) for v in all_markets.values())
    for series, mkts in sorted(all_markets.items()):
        print(f"    {series:<14} {len(mkts)} open market{'s' if len(mkts) != 1 else ''}")
    print(f"\n  Total: {total_markets} markets scanned")
    print()

    if total_markets == 0:
        print("  No open weather markets found.")
        print("  Run  python main.py --diagnose  to inspect raw API responses.")
        _show_summary([], client)
        return

    # ── Analyze ───────────────────────────────────────────────────────────
    print(f"  Running analysis (edge threshold: {EDGE_THRESHOLD:.0%})...")
    print("  Claude Opus is called for each market that clears the statistical filter.\n")

    recommendations = analyzer.analyze_all(all_markets)

    if not recommendations:
        print(f"  No markets with edge > {EDGE_THRESHOLD:.0%} found today.")
        _show_summary([], client)
        return

    n = len(recommendations)
    size = args.size
    size_note = f"  (all sizes scaled ×{size:.2g})\n" if size != 1.0 else ""
    print(f"\n  Found {n} trade {'opportunity' if n == 1 else 'opportunities'}.{size_note}\n")

    # ── Approval loop ─────────────────────────────────────────────────────
    executed: list[tuple[TradeRecommendation, dict, int]] = []
    skip_all = False
    remaining_balance = bankroll

    for i, rec in enumerate(recommendations, start=1):
        if skip_all:
            break

        price_per = rec.market_price_cents / 100
        if price_per > 0 and remaining_balance < price_per:
            print(
                f"\n  [{i}/{n}] Skipping {rec.spec.ticker} — "
                f"insufficient balance (${remaining_balance:.2f} remaining, "
                f"${price_per:.2f}/contract needed)."
            )
            continue

        contracts, risk, max_gain, ev = _show_recommendation(rec, i, n, size, balance_cap=remaining_balance)
        choice = _prompt_choice("Place this trade?")

        if choice == "y":
            try:
                order = client.orders.place_order(
                    ticker=rec.spec.ticker,
                    side=rec.side,
                    action="buy",
                    order_type="limit",
                    count=contracts,
                    yes_price=rec.market_price_cents if rec.side == "yes" else None,
                    no_price=rec.market_price_cents if rec.side == "no" else None,
                )
                executed.append((rec, order, contracts))
                tracker.record_trade(rec, order)
                remaining_balance -= risk
                print(
                    f"\n  Placed: {contracts}x {rec.side.upper()} "
                    f"on {rec.spec.ticker} @ {rec.market_price_cents}¢"
                    f"  (${remaining_balance:.2f} remaining)"
                )
            except Exception as exc:
                print(f"\n  Order failed: {exc}")

        elif choice == "s":
            skip_all = True

    # ── Summary ───────────────────────────────────────────────────────────
    _show_summary(executed, client)


if __name__ == "__main__":
    main()
