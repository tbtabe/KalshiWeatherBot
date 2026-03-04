"""
Open-Meteo forecast client.

Free, no API key required, global coverage.
Docs: https://open-meteo.com/en/docs

Returns daily high/low temperatures (°F) and max precipitation probability
for a specific date.  Also maps WMO weather codes to human-readable summaries.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from .models import DailyForecast

log = logging.getLogger(__name__)

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 10

# WMO Weather Interpretation Codes → short description
# https://open-meteo.com/en/docs#weathervariables
_WMO_DESCRIPTIONS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ slight hail",
    99: "Thunderstorm w/ heavy hail",
}


class OpenMeteoError(RuntimeError):
    pass


def get_forecast(
    lat: float,
    lon: float,
    target_date: str,
    timezone: str = "auto",
) -> Optional[DailyForecast]:
    """
    Fetch a daily forecast from Open-Meteo.

    Args:
        lat, lon:    Coordinates (global coverage).
        target_date: ISO-8601 date string, e.g. "2025-12-24".
                     Must be within the 16-day forecast window.
        timezone:    IANA timezone string or "auto" (detect from coordinates).

    Returns:
        DailyForecast or None if the date is not in the response.

    Raises:
        OpenMeteoError: On API failure.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ",".join([
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_probability_max",
            "weather_code",
        ]),
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "timezone": timezone,
        "start_date": target_date,
        "end_date": target_date,
    }

    try:
        resp = requests.get(_FORECAST_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise OpenMeteoError(f"Open-Meteo request failed: {exc}") from exc

    data = resp.json()
    daily = data.get("daily", {})
    dates = daily.get("time", [])

    if not dates or dates[0] != target_date:
        log.debug("Open-Meteo: target date %s not in response dates %s", target_date, dates)
        return None

    def _first(key: str) -> Optional[float]:
        vals = daily.get(key, [])
        return float(vals[0]) if vals and vals[0] is not None else None

    wmo_code = daily.get("weather_code", [None])[0]
    summary = _WMO_DESCRIPTIONS.get(int(wmo_code), f"WMO code {wmo_code}") if wmo_code is not None else None

    return DailyForecast(
        date=target_date,
        source="open_meteo",
        temp_high_f=_first("temperature_2m_max"),
        temp_low_f=_first("temperature_2m_min"),
        precip_probability=_first("precipitation_probability_max"),
        summary=summary,
    )
