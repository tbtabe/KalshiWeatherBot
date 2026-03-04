"""
Statistical probability estimation from weather forecast data.

Converts a point temperature forecast into P(high >= threshold) using a
normal distribution centered on the forecast mean, with sigma estimated
from inter-source spread or historical NWS RMSE for day-1 forecasts (~4°F).
"""

from __future__ import annotations

from math import erf, sqrt
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from weather.models import WeatherReport


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    """P(X <= x) where X ~ N(mu, sigma)."""
    return (1.0 + erf((x - mu) / (sigma * sqrt(2.0)))) / 2.0


def estimate_probability(
    weather: Any,
    threshold_f: float,
    condition: str = "above",
    upper_bound_f: float | None = None,
    variable: str = "high",
) -> float:
    """
    Estimate the probability that the daily temperature meets the given condition.

    Args:
        weather:       WeatherReport with one or more DailyForecast entries.
        threshold_f:   Lower bound (°F). For "above"/"below" this is the sole threshold.
        condition:     "above"   → P(temp >= threshold)
                       "below"   → P(temp <= threshold)
                       "between" → P(threshold <= temp < upper_bound_f)
        upper_bound_f: Required when condition == "between".
        variable:      "high" → use temp_high_f; "low" → use temp_low_f.

    Returns:
        Probability in [0.01, 0.99].
    """
    if variable == "low":
        # Prefer Open-Meteo: temperature_2m_min covers the full calendar day (00:00–23:59),
        # matching Kalshi's settlement period. NWS nighttime (6pm–6am+1) misses midnight–6am
        # of the target date, where the daily low commonly occurs.
        highs = [fc.temp_low_f for fc in weather.forecasts
                 if fc.source == "open_meteo" and fc.temp_low_f is not None]
        if not highs:  # fallback: Open-Meteo unavailable
            highs = [fc.temp_low_f for fc in weather.forecasts
                     if fc.temp_low_f is not None]
    else:
        highs = [fc.temp_high_f for fc in weather.forecasts
                 if fc.temp_high_f is not None]

    if not highs:
        return 0.50  # no data, maximum uncertainty

    mean_high = sum(highs) / len(highs)

    # Estimate forecast sigma:
    # - If two sources agree closely → tight sigma (floor 2.5°F)
    # - If sources diverge         → use spread / 2 as extra uncertainty
    # - Single source              → historical NWS RMSE ~4°F
    # Note: for variable=="low", we use a single source (Open-Meteo) by design —
    # using NWS/OM spread as sigma would be misleading since the divergence reflects
    # a period mismatch (NWS misses midnight–6am), not genuine forecast uncertainty.
    if variable != "low" and len(highs) >= 2:
        spread = abs(highs[0] - highs[1])
        sigma = max(spread / 2.0, 2.5)
    else:
        sigma = 4.0

    if condition == "between" and upper_bound_f is not None:
        prob = (
            _normal_cdf(upper_bound_f, mean_high, sigma)
            - _normal_cdf(threshold_f, mean_high, sigma)
        )
    elif condition == "above":
        prob = 1.0 - _normal_cdf(threshold_f, mean_high, sigma)
    else:
        prob = _normal_cdf(threshold_f, mean_high, sigma)

    # Clamp to avoid degenerate values
    return max(0.01, min(0.99, prob))
