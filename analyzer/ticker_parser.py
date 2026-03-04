"""
Parse Kalshi weather market tickers into structured MarketSpec objects.

Ticker format:  {SERIES}-{YY}{MON}{DD}-T{TEMP}
Example:        KXHIGHNY-26FEB27-T45

  SERIES  — identifies the city and variable (e.g. "KXHIGHNY" = NYC high temp)
  YY      — two-digit year (interpreted as 20YY)
  MON     — three-letter month abbreviation
  DD      — two-digit day
  TEMP    — integer temperature threshold in °F

Assumption: YES wins if the daily high temperature is AT OR ABOVE the threshold.
Verify this against live Kalshi market descriptions before trading.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .models import MarketSpec

# Map Kalshi series ticker → (geocodable city name, short display label)
SERIES_MAP: dict[str, tuple[str, str]] = {
    "KXHIGHNY":   ("New York, NY",       "NYC"),
    "KXHIGHLAX":  ("Los Angeles, CA",    "LA"),
    "KXHIGHCHI":  ("Chicago, IL",        "Chicago"),
    "KXHIGHHOU":  ("Houston, TX",        "Houston"),
    "KXHIGHPHX":  ("Phoenix, AZ",        "Phoenix"),
    "KXHIGHMIA":  ("Miami, FL",          "Miami"),
    "KXHIGHBOS":  ("Boston, MA",         "Boston"),
    "KXHIGHDAL":  ("Dallas, TX",         "Dallas"),
    "KXHIGHSEA":  ("Seattle, WA",        "Seattle"),
    "KXHIGHDEN":  ("Denver, CO",         "Denver"),
    "KXHIGHSF":   ("San Francisco, CA",  "SF"),
    "KXHIGHDC":   ("Washington, DC",     "DC"),
    "KXHIGHATL":  ("Atlanta, GA",        "Atlanta"),
    "KXHIGHDET":  ("Detroit, MI",        "Detroit"),
    "KXHIGHMSP":  ("Minneapolis, MN",    "Minneapolis"),
    "KXHIGHPHL":  ("Philadelphia, PA",   "Philadelphia"),
    "KXHIGHCLT":  ("Charlotte, NC",      "Charlotte"),
    "KXHIGHSTL":  ("St. Louis, MO",      "St. Louis"),
    "KXHIGHPIT":  ("Pittsburgh, PA",     "Pittsburgh"),
    "KXHIGHCLE":  ("Cleveland, OH",      "Cleveland"),
    "KXHIGHKC":   ("Kansas City, MO",    "Kansas City"),
    "KXHIGHSAT":  ("San Antonio, TX",    "San Antonio"),
    "KXHIGHSD":   ("San Diego, CA",      "San Diego"),
    "KXHIGHPDX":  ("Portland, OR",       "Portland"),
    "KXHIGHLV":   ("Las Vegas, NV",      "Las Vegas"),
    "KXHIGHNSH":  ("Nashville, TN",      "Nashville"),
    "KXHIGHMEM":  ("Memphis, TN",        "Memphis"),
    "KXHIGHBAL":  ("Baltimore, MD",      "Baltimore"),
    "KXHIGHNO":   ("New Orleans, LA",    "New Orleans"),
    "KXHIGHSLC":  ("Salt Lake City, UT", "Salt Lake City"),
    "KXHIGHOKC":  ("Oklahoma City, OK",  "Oklahoma City"),
    "KXHIGHRDU":  ("Raleigh, NC",        "Raleigh"),
    "KXHIGHJAX":  ("Jacksonville, FL",   "Jacksonville"),
    "KXHIGHAUS":  ("Austin, TX",         "Austin"),
    "KXHIGHIND":  ("Indianapolis, IN",   "Indianapolis"),
    "KXHIGHCOL":  ("Columbus, OH",       "Columbus"),
    "KXHIGHBUF":  ("Buffalo, NY",        "Buffalo"),
    "KXHIGHALB":  ("Albany, NY",         "Albany"),
    # Low-temperature series (KXLOWT*) — same cities, daily low instead of daily high
    "KXLOWTNY":   ("New York, NY",       "NYC"),
    "KXLOWTLAX":  ("Los Angeles, CA",    "LA"),
    "KXLOWTCHI":  ("Chicago, IL",        "Chicago"),
    "KXLOWTHOU":  ("Houston, TX",        "Houston"),
    "KXLOWTPHX":  ("Phoenix, AZ",        "Phoenix"),
    "KXLOWTMIA":  ("Miami, FL",          "Miami"),
    "KXLOWTBOS":  ("Boston, MA",         "Boston"),
    "KXLOWTDAL":  ("Dallas, TX",         "Dallas"),
    "KXLOWTSEA":  ("Seattle, WA",        "Seattle"),
    "KXLOWTDEN":  ("Denver, CO",         "Denver"),
    "KXLOWTSF":   ("San Francisco, CA",  "SF"),
    "KXLOWTDC":   ("Washington, DC",     "DC"),
    "KXLOWTATL":  ("Atlanta, GA",        "Atlanta"),
    "KXLOWTDET":  ("Detroit, MI",        "Detroit"),
    "KXLOWTMSP":  ("Minneapolis, MN",    "Minneapolis"),
    "KXLOWTPHL":  ("Philadelphia, PA",   "Philadelphia"),
    "KXLOWTCLT":  ("Charlotte, NC",      "Charlotte"),
    "KXLOWTSTL":  ("St. Louis, MO",      "St. Louis"),
    "KXLOWTPIT":  ("Pittsburgh, PA",     "Pittsburgh"),
    "KXLOWTCLE":  ("Cleveland, OH",      "Cleveland"),
    "KXLOWTKC":   ("Kansas City, MO",    "Kansas City"),
    "KXLOWTSAT":  ("San Antonio, TX",    "San Antonio"),
    "KXLOWTSD":   ("San Diego, CA",      "San Diego"),
    "KXLOWTPDX":  ("Portland, OR",       "Portland"),
    "KXLOWTLV":   ("Las Vegas, NV",      "Las Vegas"),
    "KXLOWTNSH":  ("Nashville, TN",      "Nashville"),
    "KXLOWTMEM":  ("Memphis, TN",        "Memphis"),
    "KXLOWTBAL":  ("Baltimore, MD",      "Baltimore"),
    "KXLOWTNO":   ("New Orleans, LA",    "New Orleans"),
    "KXLOWTSLC":  ("Salt Lake City, UT", "Salt Lake City"),
    "KXLOWTOKC":  ("Oklahoma City, OK",  "Oklahoma City"),
    "KXLOWTRDU":  ("Raleigh, NC",        "Raleigh"),
    "KXLOWTJAX":  ("Jacksonville, FL",   "Jacksonville"),
    "KXLOWTAUS":  ("Austin, TX",         "Austin"),
    "KXLOWTIND":  ("Indianapolis, IN",   "Indianapolis"),
    "KXLOWTCOL":  ("Columbus, OH",       "Columbus"),
    "KXLOWTBUF":  ("Buffalo, NY",        "Buffalo"),
    "KXLOWTALB":  ("Albany, NY",         "Albany"),
}

_TICKER_RE = re.compile(
    r"^(?P<series>KXHIGH[A-Z]+|KXLOWT[A-Z]+)-(?P<yr>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})-(?P<kind>[TB])(?P<temp>\d+(?:\.\d+)?)$"
)

_MONTH: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_ticker(ticker: str) -> Optional[MarketSpec]:
    """
    Parse a Kalshi weather ticker into a MarketSpec.

    Returns None if the ticker doesn't match the expected format or
    the series isn't in our SERIES_MAP.
    """
    m = _TICKER_RE.match(ticker.upper())
    if not m:
        return None

    series = m.group("series")
    if series not in SERIES_MAP:
        return None

    month_num = _MONTH.get(m.group("mon"))
    if month_num is None:
        return None

    try:
        year = 2000 + int(m.group("yr"))
        date_obj = datetime(year, month_num, int(m.group("day")))
        date_str = date_obj.date().isoformat()
    except ValueError:
        return None

    city, display_name = SERIES_MAP[series]
    midpoint = float(m.group("temp"))
    kind = m.group("kind")

    if kind == "B":
        # Bracket market — ticker encodes the midpoint, e.g. B76.5 → [76.0, 77.0]
        condition = "between"
        threshold_f = midpoint - 0.5
        upper_bound_f: float | None = midpoint + 0.5
    else:
        # T market — 1°F bracket where the ticker encodes the lower bound.
        # e.g. T45 → [45.0, 46.0)
        # Note: Kalshi's top bracket for a series (e.g. T83 for Miami) is displayed
        # as "84°F or above" rather than "83–84°F" since there is no upper bracket,
        # but the probability math is the same for our purposes.
        condition = "between"
        threshold_f = midpoint
        upper_bound_f = midpoint + 1.0

    variable = "low" if series.startswith("KXLOWT") else "high"

    return MarketSpec(
        ticker=ticker,
        series=series,
        city=city,
        display_name=display_name,
        date=date_str,
        threshold_f=threshold_f,
        condition=condition,
        upper_bound_f=upper_bound_f,
        variable=variable,
    )
