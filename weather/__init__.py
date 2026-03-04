"""
Weather module for KalshiBot.

Pulls forecasts from two independent sources and merges them:
  - NWS (National Weather Service) — US-only, official, detailed alerts
  - Open-Meteo — global coverage, reliable fallback

Usage:
    from weather import WeatherFetcher

    fetcher = WeatherFetcher()
    report = fetcher.get_report("New York, NY", "2025-12-24")
    print(report)
    print(f"High: {report.temp_high_f}°F")
    for alert in report.alerts:
        print(f"ALERT: {alert.event}")
"""

from __future__ import annotations

import logging
from typing import Optional

# In-process cache: avoids redundant geocoding + NWS + Open-Meteo calls when
# the analyzer scans multiple markets for the same city on the same date.
_report_cache: dict[tuple, "WeatherReport"] = {}

from .geocoding import GeocodingError, GeoLocation, geocode
from .models import DailyForecast, WeatherAlert, WeatherReport
from .nws import NWSError, NWSUnavailable, get_alerts
from .nws import get_forecast as nws_get_forecast
from .openmeteo import OpenMeteoError
from .openmeteo import get_forecast as om_get_forecast

log = logging.getLogger(__name__)

__all__ = [
    "WeatherFetcher",
    "WeatherReport",
    "DailyForecast",
    "WeatherAlert",
    "GeocodingError",
]


class WeatherFetcher:
    """
    Fetch unified weather data for a city and date.

    Source priority:
      - NWS is preferred for temperature and alerts when the city is in the US
        (country_code="US").  NWS data is considered more accurate for US
        locations and is the authoritative source for severe weather alerts.
      - Open-Meteo is always queried as a secondary source and used as the
        primary source for non-US cities or when NWS is unavailable.

    Merged values (temp_high_f, temp_low_f, precip_probability):
      - temp_high_f: NWS preferred (daytime 6am–6pm captures the daily high well).
      - temp_low_f:  Open-Meteo preferred (true 24h min, matches Kalshi's settlement
        period). NWS nighttime (6pm–6am+1) misses midnight–6am of the target date,
        where the overnight low commonly occurs.
      - precip_probability, summary: NWS preferred, Open-Meteo fills gaps.
    """

    def __init__(self, default_country_code: Optional[str] = "US") -> None:
        self._country_code = default_country_code

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_report(
        self,
        city: str,
        date: str,
        country_code: Optional[str] = None,
    ) -> WeatherReport:
        """
        Fetch a full weather report for a city on a given date.

        Args:
            city:         City name, e.g. "New York" or "Chicago, IL".
            date:         ISO-8601 date string, e.g. "2025-12-24".
                          Must be within the next 16 days (Open-Meteo limit).
            country_code: ISO-3166 alpha-2 country code to disambiguate cities.
                          Defaults to the value set in __init__ ("US").

        Returns:
            WeatherReport with merged data from NWS + Open-Meteo.

        Raises:
            GeocodingError: If the city cannot be resolved to coordinates.
        """
        cc = country_code if country_code is not None else self._country_code
        cache_key = (city.lower(), date, cc or "")
        if cache_key in _report_cache:
            log.debug("Weather cache hit: %s / %s", city, date)
            return _report_cache[cache_key]

        location = geocode(city, country_code=cc)
        log.info(
            "Resolved '%s' → %s, %s (%.4f, %.4f)",
            city, location.name, location.region, location.latitude, location.longitude,
        )

        forecasts: list[DailyForecast] = []
        alerts: list[WeatherAlert] = []

        # --- NWS (US only) ---
        nws_fc: Optional[DailyForecast] = None
        if location.country_code.upper() == "US":
            nws_fc, alerts = self._fetch_nws(location, date)
            if nws_fc:
                forecasts.append(nws_fc)

        # --- Open-Meteo (global) ---
        om_fc: Optional[DailyForecast] = None
        try:
            om_fc = om_get_forecast(
                lat=location.latitude,
                lon=location.longitude,
                target_date=date,
                timezone=location.timezone,
            )
            if om_fc:
                forecasts.append(om_fc)
        except OpenMeteoError as exc:
            log.warning("Open-Meteo unavailable: %s", exc)

        # --- Merge ---
        primary = nws_fc or om_fc
        secondary = om_fc if nws_fc else None

        # temp_high: NWS daytime preferred (6am–6pm captures the daily high well)
        temp_high = _coalesce(
            primary.temp_high_f if primary else None,
            secondary.temp_high_f if secondary else None,
        )
        # temp_low: Open-Meteo preferred (true 24h min, matches Kalshi's settlement period)
        # NWS nighttime (6pm–6am+1) misses midnight–6am of the target date, where the
        # overnight low commonly occurs.
        temp_low = _coalesce(
            om_fc.temp_low_f if om_fc else None,
            nws_fc.temp_low_f if nws_fc else None,
        )
        precip = _coalesce(
            primary.precip_probability if primary else None,
            secondary.precip_probability if secondary else None,
        )
        summary = _coalesce(
            primary.summary if primary else None,
            secondary.summary if secondary else None,
        )

        display_city = f"{location.name}, {location.region}" if location.region else location.name

        report = WeatherReport(
            city=display_city,
            date=date,
            latitude=location.latitude,
            longitude=location.longitude,
            temp_high_f=temp_high,
            temp_low_f=temp_low,
            precip_probability=precip,
            summary=summary,
            forecasts=forecasts,
            alerts=alerts,
        )
        _report_cache[cache_key] = report
        return report

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_nws(
        self, location: GeoLocation, date: str
    ) -> tuple[Optional[DailyForecast], list[WeatherAlert]]:
        fc: Optional[DailyForecast] = None
        alerts: list[WeatherAlert] = []

        try:
            fc = nws_get_forecast(location.latitude, location.longitude, date)
        except NWSUnavailable as exc:
            log.info("NWS unavailable for this location: %s", exc)
        except NWSError as exc:
            log.warning("NWS forecast failed: %s", exc)

        try:
            alerts = get_alerts(location.latitude, location.longitude)
        except Exception as exc:  # alerts are best-effort
            log.warning("NWS alerts failed: %s", exc)

        return fc, alerts


def _coalesce(*values):
    """Return the first non-None value, or None."""
    for v in values:
        if v is not None:
            return v
    return None
