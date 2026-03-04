"""
Market analyzer — wires together weather data, statistics, and Claude.

Usage:
    from analyzer import Analyzer

    az = Analyzer(api_key="sk-ant-...")
    all_markets = az.fetch_all_markets(kalshi_client)
    recs = az.analyze_all(all_markets)
    for rec in recs:
        print(rec.spec.ticker, rec.edge, rec.reasoning)
"""

from __future__ import annotations

import logging
from datetime import date as _date, datetime as _datetime
from typing import TYPE_CHECKING

from .claude_analyst import ClaudeAnalyst
from .models import MarketSpec, TradeRecommendation
from .probability import estimate_probability
from .ticker_parser import SERIES_MAP, parse_ticker

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

__all__ = ["Analyzer", "TradeRecommendation", "MarketSpec"]

_SAME_DAY_CUTOFF_HOUR = 19  # 7 PM local time — daily high is always recorded by then


def _same_day_past_cutoff(city: str) -> bool:
    """Return True if local time in the city is past the same-day cutoff hour."""
    from zoneinfo import ZoneInfo
    try:
        from weather.geocoding import geocode
        loc = geocode(city)
        tz = ZoneInfo(loc.timezone)
        return _datetime.now(tz).hour >= _SAME_DAY_CUTOFF_HOUR
    except Exception:
        return False  # unknown timezone — let the market through


class Analyzer:
    """
    Orchestrates the full analysis pipeline for a set of Kalshi weather markets.

    Pipeline per market:
      1. Parse ticker → MarketSpec
      2. Fetch weather forecast (NWS + Open-Meteo)
      3. Compute statistical probability via normal distribution
      4. If edge > threshold: call Claude for refined estimate + reasoning
      5. Re-check edge with Claude's probability
      6. Size position using ¼-Kelly criterion (capped)
    """

    def __init__(
        self,
        api_key: str,
        bankroll: float = 1_000.0,
        max_contracts: int = 25,
        max_risk_dollars: float = 100.0,
        edge_threshold: float = 0.15,
    ) -> None:
        # Import here to avoid loading at module level unnecessarily
        from weather import WeatherFetcher

        self._fetcher = WeatherFetcher()
        self._claude = ClaudeAnalyst(api_key=api_key)
        self._bankroll = bankroll
        self._max_contracts = max_contracts
        self._max_risk_dollars = max_risk_dollars
        self._edge_threshold = edge_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_all_markets(self, client) -> dict[str, list[dict]]:
        """
        Fetch open markets for every known weather series.

        Returns {series_ticker: [market_dict, ...]}.
        Series with no open markets are omitted.
        """
        from weather import GeocodingError  # noqa: F401 (ensure module loads)

        result: dict[str, list[dict]] = {}
        first_error: Exception | None = None

        for series in SERIES_MAP:
            try:
                markets = client.markets.get_weather_markets(series_ticker=series)
                if markets:
                    result[series] = markets
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                log.debug("Could not fetch series %s: %s", series, exc)

        # If nothing came back, surface a diagnostic hint.
        if not result:
            if first_error is not None:
                log.warning(
                    "All series queries failed. First error: %s", first_error
                )
                # Re-raise so the caller can show a meaningful message.
                raise RuntimeError(
                    f"Kalshi API unreachable or auth failed: {first_error}"
                ) from first_error
            else:
                # Queries succeeded but returned empty — series tickers may be stale.
                log.warning(
                    "All %d series returned 0 markets. "
                    "The Kalshi series tickers in SERIES_MAP may no longer match "
                    "the live API. Run with --diagnose to inspect raw tickers.",
                    len(SERIES_MAP),
                )

        return result

    def analyze_best(self, all_markets: dict[str, list[dict]]) -> TradeRecommendation | None:
        """
        Find the single market with the highest statistical edge across all series,
        run Claude on it, and return a recommendation regardless of edge threshold.

        Returns None if no parseable, priceable markets are found.
        """
        from weather import GeocodingError

        # --- Pass 1: score every market statistically, keep the best ---
        best_stat_edge = float("-inf")
        best: tuple | None = None  # (stat_edge, side, price_cents, spec, weather, stat_prob, market)

        for series, markets in all_markets.items():
            for market in markets:
                ticker = market.get("ticker", "")
                spec = parse_ticker(ticker)
                if spec is None:
                    continue

                today_str = _date.today().isoformat()
                if spec.date < today_str:
                    log.debug("Skipping %s — settlement date %s is in the past", ticker, spec.date)
                    continue
                if spec.date == today_str and _same_day_past_cutoff(spec.city):
                    log.debug("Skipping %s — same-day market, local time past %d:00", ticker, _SAME_DAY_CUTOFF_HOUR)
                    continue

                try:
                    weather = self._fetcher.get_report(spec.city, spec.date)
                except (GeocodingError, Exception) as exc:
                    log.debug("Weather fetch failed for %s: %s", ticker, exc)
                    continue

                stat_prob = estimate_probability(weather, spec.threshold_f, spec.condition, spec.upper_bound_f, spec.variable)

                yes_ask = market.get("yes_ask")
                if yes_ask is None:
                    continue
                no_ask = market.get("no_ask")
                if no_ask is None:
                    yes_bid = market.get("yes_bid", yes_ask)
                    no_ask = max(1, 100 - yes_bid)

                edge_yes = stat_prob - yes_ask / 100
                edge_no  = (1 - stat_prob) - no_ask / 100

                if edge_yes >= edge_no:
                    side, edge, price_cents = "yes", edge_yes, yes_ask
                else:
                    side, edge, price_cents = "no", edge_no, no_ask

                if edge > best_stat_edge:
                    best_stat_edge = edge
                    best = (edge, side, price_cents, spec, weather, stat_prob, market)

        if best is None:
            return None

        _, side, price_cents, spec, weather, stat_prob, market = best

        # --- Pass 2: Claude analysis on the winner ---
        analysis = self._claude.analyze(spec, weather, stat_prob, market)

        cp = analysis.probability_estimate
        claude_edge = (
            cp - price_cents / 100
            if side == "yes"
            else (1 - cp) - price_cents / 100
        )

        kelly = claude_edge / (1 - price_cents / 100) if claude_edge > 0 else 0
        wager = 0.25 * kelly * self._bankroll
        contracts = max(1, min(
            int(wager / max(price_cents / 100, 0.01)),
            int(self._max_risk_dollars / max(price_cents / 100, 0.01)),
            self._max_contracts,
        ))

        p_win = cp if side == "yes" else (1 - cp)
        risk     = contracts * price_cents / 100
        max_gain = contracts * (100 - price_cents) / 100
        ev       = contracts * (
            p_win * (100 - price_cents) - (1 - p_win) * price_cents
        ) / 100

        return TradeRecommendation(
            spec=spec,
            market=market,
            weather=weather,
            stat_probability=stat_prob,
            claude_probability=cp,
            confidence=analysis.confidence,
            reasoning=analysis.reasoning,
            side=side,
            market_price_cents=price_cents,
            edge=claude_edge,
            suggested_contracts=contracts,
            risk_dollars=risk,
            max_gain_dollars=max_gain,
            expected_value_dollars=ev,
        )

    def analyze_all(self, all_markets: dict[str, list[dict]]) -> list[TradeRecommendation]:
        """
        Analyze every market, filter by edge, and return recommendations sorted
        by edge (best first). Only markets exceeding edge_threshold after Claude's
        review are included.
        """
        from weather import GeocodingError

        recs: list[TradeRecommendation] = []

        for series, markets in all_markets.items():
            for market in markets:
                ticker = market.get("ticker", "")

                spec = parse_ticker(ticker)
                if spec is None:
                    continue

                today_str = _date.today().isoformat()
                if spec.date < today_str:
                    log.debug("Skipping %s — settlement date %s is in the past", ticker, spec.date)
                    continue
                if spec.date == today_str and _same_day_past_cutoff(spec.city):
                    log.debug("Skipping %s — same-day market, local time past %d:00", ticker, _SAME_DAY_CUTOFF_HOUR)
                    continue

                # --- Weather ---
                try:
                    weather = self._fetcher.get_report(spec.city, spec.date)
                except GeocodingError as exc:
                    log.debug("Geocoding failed for %s: %s", ticker, exc)
                    continue
                except Exception as exc:
                    log.debug("Weather fetch failed for %s: %s", ticker, exc)
                    continue

                # --- Statistical edge ---
                stat_prob = estimate_probability(weather, spec.threshold_f, spec.condition, spec.upper_bound_f, spec.variable)

                yes_ask = market.get("yes_ask")
                if yes_ask is None:
                    continue

                no_ask = market.get("no_ask")
                if no_ask is None:
                    yes_bid = market.get("yes_bid", yes_ask)
                    no_ask = max(1, 100 - yes_bid)

                edge_yes = stat_prob - yes_ask / 100
                edge_no  = (1 - stat_prob) - no_ask / 100

                # Pick the better side
                if edge_yes >= edge_no:
                    side, edge, price_cents = "yes", edge_yes, yes_ask
                else:
                    side, edge, price_cents = "no", edge_no, no_ask

                if edge < self._edge_threshold:
                    continue  # not worth Claude's time

                log.info("%s  stat_prob=%.1f%%  edge=+%.1f%%  side=%s",
                         ticker, stat_prob * 100, edge * 100, side)

                # --- Claude analysis ---
                analysis = self._claude.analyze(spec, weather, stat_prob, market)

                # Re-evaluate edge with Claude's probability
                cp = analysis.probability_estimate
                claude_edge = (
                    cp - price_cents / 100
                    if side == "yes"
                    else (1 - cp) - price_cents / 100
                )

                if claude_edge < self._edge_threshold or not analysis.recommend_trade:
                    log.info("%s  Claude edge %.1f%% below threshold — skipping",
                             ticker, claude_edge * 100)
                    continue

                # --- Position sizing: ¼ Kelly ---
                kelly = claude_edge / (1 - price_cents / 100)
                wager = 0.25 * kelly * self._bankroll  # dollars at risk
                contracts = min(
                    max(1, int(wager / (price_cents / 100))),
                    int(self._max_risk_dollars / max(price_cents / 100, 0.01)),
                    self._max_contracts,
                )

                p_win = cp if side == "yes" else (1 - cp)
                risk      = contracts * price_cents / 100
                max_gain  = contracts * (100 - price_cents) / 100
                ev        = contracts * (
                    p_win * (100 - price_cents) - (1 - p_win) * price_cents
                ) / 100

                recs.append(TradeRecommendation(
                    spec=spec,
                    market=market,
                    weather=weather,
                    stat_probability=stat_prob,
                    claude_probability=cp,
                    confidence=analysis.confidence,
                    reasoning=analysis.reasoning,
                    side=side,
                    market_price_cents=price_cents,
                    edge=claude_edge,
                    suggested_contracts=contracts,
                    risk_dollars=risk,
                    max_gain_dollars=max_gain,
                    expected_value_dollars=ev,
                ))

        return sorted(recs, key=lambda r: r.edge, reverse=True)
