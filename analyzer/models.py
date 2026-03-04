"""Shared data models for the analyzer pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from weather.models import WeatherReport


@dataclass
class MarketSpec:
    """Parsed information extracted from a Kalshi weather market ticker."""

    ticker: str
    series: str          # e.g. "HIGHNY"
    city: str            # geocodable city name, e.g. "New York, NY"
    display_name: str    # short label for UI, e.g. "NYC"
    date: str            # ISO-8601 date the market resolves
    threshold_f: float        # lower bound (°F); for "above" markets this is the sole threshold
    condition: str = "above"  # "above" → YES if high >= threshold
                              # "between" → YES if threshold <= high < upper_bound_f
    upper_bound_f: float | None = None  # upper bound for "between" markets only
    variable: str = "high"   # "high" | "low"


@dataclass
class TradeRecommendation:
    """A single trade opportunity surfaced by the analyzer."""

    spec: MarketSpec
    market: dict              # raw market dict from Kalshi API
    weather: Any              # WeatherReport
    stat_probability: float   # statistical estimate of P(YES), 0–1
    claude_probability: float # Claude's refined estimate, 0–1
    confidence: str           # "high" | "medium" | "low"
    reasoning: str            # Claude's 2–3 sentence analysis
    side: str                 # "yes" | "no" — which side to buy
    market_price_cents: int   # price we pay per contract (cents)
    edge: float               # (our_prob – market_prob), 0–1
    suggested_contracts: int
    risk_dollars: float       # max loss on this position
    max_gain_dollars: float   # max win on this position
    expected_value_dollars: float
