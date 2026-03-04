"""Shared data models for weather forecasts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DailyForecast:
    """Temperature and precipitation data for one day from one source."""

    date: str                          # ISO-8601 date, e.g. "2025-12-24"
    source: str                        # "nws" | "open_meteo"
    temp_high_f: Optional[float]       # High temperature in °F
    temp_low_f: Optional[float]        # Low temperature in °F
    precip_probability: Optional[float]  # 0–100 %
    summary: Optional[str] = None      # Short human-readable description


@dataclass
class WeatherAlert:
    """A severe-weather alert from NWS."""

    event: str                         # e.g. "Winter Storm Warning"
    severity: str                      # "Minor" | "Moderate" | "Severe" | "Extreme"
    headline: str
    description: str
    expires: Optional[str]             # ISO-8601 datetime or None
    source: str = "nws"


@dataclass
class WeatherReport:
    """
    Unified weather report for a city on a specific date.

    `temp_high_f`, `temp_low_f`, and `precip_probability` are consolidated
    best-estimate values derived from available sources (NWS preferred for
    US locations; Open-Meteo used as fallback or when NWS is unavailable).
    """

    city: str
    date: str                          # ISO-8601 date
    latitude: float
    longitude: float
    temp_high_f: Optional[float]
    temp_low_f: Optional[float]
    precip_probability: Optional[float]  # 0–100 %
    summary: Optional[str]
    forecasts: list[DailyForecast] = field(default_factory=list)
    alerts: list[WeatherAlert] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"Weather report for {self.city} on {self.date}",
            f"  High: {self.temp_high_f:.1f}°F" if self.temp_high_f is not None else "  High: N/A",
            f"  Low:  {self.temp_low_f:.1f}°F" if self.temp_low_f is not None else "  Low:  N/A",
            f"  Precip probability: {self.precip_probability:.0f}%"
            if self.precip_probability is not None
            else "  Precip probability: N/A",
        ]
        if self.summary:
            lines.append(f"  Summary: {self.summary}")
        if self.alerts:
            lines.append(f"  Alerts ({len(self.alerts)}):")
            for a in self.alerts:
                lines.append(f"    [{a.severity}] {a.event}: {a.headline}")
        return "\n".join(lines)
