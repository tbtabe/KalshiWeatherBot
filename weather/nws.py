"""
National Weather Service (NWS) API client.

Free, no key required, US-only coverage.
Docs: https://www.weather.gov/documentation/services-web-api

Flow:
  1. GET /points/{lat},{lon}  → forecast office + grid coordinates
  2. GET /gridpoints/{office}/{gridX},{gridY}/forecast → daily periods
  3. GET /alerts/active?point={lat},{lon}              → active alerts
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

import requests

from .models import DailyForecast, WeatherAlert

log = logging.getLogger(__name__)

_BASE = "https://api.weather.gov"
_TIMEOUT = 15
_HEADERS = {
    # NWS requires a descriptive User-Agent identifying the application
    "User-Agent": "KalshiWeatherBot/1.0 (weather trading bot; contact: your@email.com)",
    "Accept": "application/geo+json",
}


class NWSError(RuntimeError):
    pass


class NWSUnavailable(NWSError):
    """Raised when coordinates fall outside NWS coverage (non-US locations)."""
    pass


def _get(url: str, params: Optional[dict] = None) -> dict:
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code == 404:
            raise NWSUnavailable(f"NWS returned 404 for {url} — location may be outside US coverage.")
        resp.raise_for_status()
        return resp.json()
    except NWSUnavailable:
        raise
    except requests.RequestException as exc:
        raise NWSError(f"NWS request failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_grid(lat: float, lon: float) -> tuple[str, int, int]:
    """
    Return (office, gridX, gridY) for a lat/lon pair.
    Raises NWSUnavailable for non-US coordinates.
    """
    data = _get(f"{_BASE}/points/{lat:.4f},{lon:.4f}")
    props = data["properties"]
    return props["gridId"], props["gridX"], props["gridY"]


def _parse_nws_temp(value: Optional[dict]) -> Optional[float]:
    """Convert a NWS unitCode quantity dict to °F, or return None."""
    if value is None:
        return None
    v = value.get("value")
    if v is None:
        return None
    unit = value.get("unitCode", "")
    if "degC" in unit or "celsius" in unit.lower():
        return v * 9 / 5 + 32
    return float(v)  # already °F


def _period_date(period: dict) -> str:
    """Extract ISO-8601 date string from a NWS forecast period."""
    start = period.get("startTime", "")
    try:
        return datetime.fromisoformat(start).date().isoformat()
    except ValueError:
        return start[:10]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_forecast(lat: float, lon: float, target_date: str) -> Optional[DailyForecast]:
    """
    Fetch the NWS daily forecast for a given date.

    Args:
        lat, lon:    Coordinates (must be within the US).
        target_date: ISO-8601 date string, e.g. "2025-12-24".

    Returns:
        DailyForecast or None if no period matched the target date.

    Raises:
        NWSUnavailable: If coordinates are outside NWS coverage.
        NWSError:       On any other API failure.
    """
    office, grid_x, grid_y = _resolve_grid(lat, lon)
    data = _get(f"{_BASE}/gridpoints/{office}/{grid_x},{grid_y}/forecast")
    periods = data["properties"]["periods"]

    high_f: Optional[float] = None
    low_f: Optional[float] = None
    precip: Optional[float] = None
    summary: Optional[str] = None

    for period in periods:
        if _period_date(period) != target_date:
            continue

        raw_temp = period.get("temperature")
        raw_unit = period.get("temperatureUnit", "F")
        if raw_temp is not None:
            temp_f = float(raw_temp) if raw_unit == "F" else float(raw_temp) * 9 / 5 + 32
        else:
            temp_f = None

        # NWS periods are 12-hour blocks. The nighttime period for a given calendar date
        # (startTime ~18:00) spans 6pm that day through 6am the next day, so it does NOT
        # cover midnight–6am of the target date. Use Open-Meteo for daily minimum
        # temperature when full-day (00:00–23:59) coverage is required.
        is_daytime = period.get("isDaytime", True)
        if is_daytime and temp_f is not None:
            high_f = temp_f
        elif not is_daytime and temp_f is not None:
            low_f = temp_f

        # Precipitation probability (added in newer NWS API versions)
        prob = period.get("probabilityOfPrecipitation", {})
        if isinstance(prob, dict):
            pv = prob.get("value")
            if pv is not None and precip is None:
                precip = float(pv)

        # Use daytime short forecast as the summary
        if is_daytime and not summary:
            summary = period.get("shortForecast")

    if high_f is None and low_f is None:
        log.debug("NWS: no periods matched date %s", target_date)
        return None

    return DailyForecast(
        date=target_date,
        source="nws",
        temp_high_f=high_f,
        temp_low_f=low_f,
        precip_probability=precip,
        summary=summary,
    )


def get_alerts(lat: float, lon: float) -> list[WeatherAlert]:
    """
    Fetch active NWS severe-weather alerts for a location.

    Returns an empty list (not an error) for non-US locations.
    """
    try:
        data = _get(f"{_BASE}/alerts/active", params={"point": f"{lat:.4f},{lon:.4f}"})
    except (NWSUnavailable, NWSError) as exc:
        log.warning("NWS alerts unavailable: %s", exc)
        return []

    alerts: list[WeatherAlert] = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        alerts.append(
            WeatherAlert(
                event=props.get("event", "Unknown Event"),
                severity=props.get("severity", "Unknown"),
                headline=props.get("headline") or props.get("event", ""),
                description=(props.get("description") or "").strip(),
                expires=props.get("expires"),
                source="nws",
            )
        )
    return alerts
