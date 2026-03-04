"""
Geocoding: city name → (latitude, longitude, canonical name).

Uses the Open-Meteo Geocoding API — free, no key required, global coverage.
Docs: https://open-meteo.com/en/docs/geocoding-api
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_TIMEOUT = 10  # seconds


@dataclass
class GeoLocation:
    latitude: float
    longitude: float
    name: str            # canonical city name from API
    region: str          # state / province
    country: str
    country_code: str    # ISO-3166 alpha-2, e.g. "US"
    timezone: str        # e.g. "America/New_York"


# ---------------------------------------------------------------------------
# Hardcoded NOAA ASOS station coordinates for every Kalshi weather series.
#
# Kalshi settles against the NWS Climatological Report (Daily) for a specific
# named station, which is listed in each market's rules_primary field.
# Using the correct station coordinates ensures our NWS and Open-Meteo
# forecasts are for the exact measurement location, not a city centroid.
#
# Stations marked [CONFIRMED] were verified from live Kalshi market rules.
# Stations marked [ASSUMED] use the primary airport ASOS station and should
# be verified against open market rules_primary when those series become active.
# ---------------------------------------------------------------------------
_KALSHI_STATIONS: dict[str, GeoLocation] = {
    # [CONFIRMED] Central Park, New York
    "New York, NY":       GeoLocation(40.7789, -73.9692, "New York (Central Park)", "New York", "United States", "US", "America/New_York"),
    # [CONFIRMED] Los Angeles Airport (LAX)
    "Los Angeles, CA":    GeoLocation(33.9425, -118.4081, "Los Angeles (LAX)", "California", "United States", "US", "America/Los_Angeles"),
    # [CONFIRMED] Chicago Midway (MDW) — NOT O'Hare
    "Chicago, IL":        GeoLocation(41.7868, -87.7522, "Chicago (Midway)", "Illinois", "United States", "US", "America/Chicago"),
    # [CONFIRMED] Miami International Airport (MIA)
    "Miami, FL":          GeoLocation(25.7959, -80.2870, "Miami (MIA)", "Florida", "United States", "US", "America/New_York"),
    # [CONFIRMED] Austin Bergstrom International (AUS)
    "Austin, TX":         GeoLocation(30.1975, -97.6664, "Austin (AUS)", "Texas", "United States", "US", "America/Chicago"),
    # [CONFIRMED] Denver International Airport (DEN)
    "Denver, CO":         GeoLocation(39.8561, -104.6737, "Denver (DEN)", "Colorado", "United States", "US", "America/Denver"),

    # [ASSUMED] — verify against rules_primary when series opens
    "Houston, TX":        GeoLocation(29.9902, -95.3368, "Houston (IAH)", "Texas", "United States", "US", "America/Chicago"),
    "Phoenix, AZ":        GeoLocation(33.4373, -112.0078, "Phoenix (PHX)", "Arizona", "United States", "US", "America/Phoenix"),
    "Boston, MA":         GeoLocation(42.3656, -71.0096, "Boston (BOS)", "Massachusetts", "United States", "US", "America/New_York"),
    "Dallas, TX":         GeoLocation(32.8998, -97.0403, "Dallas (DFW)", "Texas", "United States", "US", "America/Chicago"),
    "Seattle, WA":        GeoLocation(47.4502, -122.3088, "Seattle (SEA)", "Washington", "United States", "US", "America/Los_Angeles"),
    "San Francisco, CA":  GeoLocation(37.6213, -122.3790, "San Francisco (SFO)", "California", "United States", "US", "America/Los_Angeles"),
    "Washington, DC":     GeoLocation(38.8521, -77.0377, "Washington (DCA)", "District of Columbia", "United States", "US", "America/New_York"),
    "Atlanta, GA":        GeoLocation(33.6407, -84.4277, "Atlanta (ATL)", "Georgia", "United States", "US", "America/New_York"),
    "Detroit, MI":        GeoLocation(42.2162, -83.3554, "Detroit (DTW)", "Michigan", "United States", "US", "America/Detroit"),
    "Minneapolis, MN":    GeoLocation(44.8848, -93.2223, "Minneapolis (MSP)", "Minnesota", "United States", "US", "America/Chicago"),
    "Philadelphia, PA":   GeoLocation(39.8744, -75.2424, "Philadelphia (PHL)", "Pennsylvania", "United States", "US", "America/New_York"),
    "Charlotte, NC":      GeoLocation(35.2140, -80.9431, "Charlotte (CLT)", "North Carolina", "United States", "US", "America/New_York"),
    "St. Louis, MO":      GeoLocation(38.7487, -90.3700, "St. Louis (STL)", "Missouri", "United States", "US", "America/Chicago"),
    "Pittsburgh, PA":     GeoLocation(40.4916, -80.2329, "Pittsburgh (PIT)", "Pennsylvania", "United States", "US", "America/New_York"),
    "Cleveland, OH":      GeoLocation(41.4058, -81.8539, "Cleveland (CLE)", "Ohio", "United States", "US", "America/New_York"),
    "Kansas City, MO":    GeoLocation(39.2976, -94.7139, "Kansas City (MCI)", "Missouri", "United States", "US", "America/Chicago"),
    "San Antonio, TX":    GeoLocation(29.5337, -98.4698, "San Antonio (SAT)", "Texas", "United States", "US", "America/Chicago"),
    "San Diego, CA":      GeoLocation(32.7338, -117.1933, "San Diego (SAN)", "California", "United States", "US", "America/Los_Angeles"),
    "Portland, OR":       GeoLocation(45.5898, -122.5951, "Portland (PDX)", "Oregon", "United States", "US", "America/Los_Angeles"),
    "Las Vegas, NV":      GeoLocation(36.0840, -115.1537, "Las Vegas (LAS)", "Nevada", "United States", "US", "America/Los_Angeles"),
    "Nashville, TN":      GeoLocation(36.1245, -86.6782, "Nashville (BNA)", "Tennessee", "United States", "US", "America/Chicago"),
    "Memphis, TN":        GeoLocation(35.0421, -89.9792, "Memphis (MEM)", "Tennessee", "United States", "US", "America/Chicago"),
    "Baltimore, MD":      GeoLocation(39.1774, -76.6684, "Baltimore (BWI)", "Maryland", "United States", "US", "America/New_York"),
    "New Orleans, LA":    GeoLocation(29.9934, -90.2580, "New Orleans (MSY)", "Louisiana", "United States", "US", "America/Chicago"),
    "Salt Lake City, UT": GeoLocation(40.7884, -111.9778, "Salt Lake City (SLC)", "Utah", "United States", "US", "America/Denver"),
    "Oklahoma City, OK":  GeoLocation(35.3931, -97.6007, "Oklahoma City (OKC)", "Oklahoma", "United States", "US", "America/Chicago"),
    "Raleigh, NC":        GeoLocation(35.8801, -78.7880, "Raleigh (RDU)", "North Carolina", "United States", "US", "America/New_York"),
    "Jacksonville, FL":   GeoLocation(30.4941, -81.6879, "Jacksonville (JAX)", "Florida", "United States", "US", "America/New_York"),
    "Indianapolis, IN":   GeoLocation(39.7173, -86.2944, "Indianapolis (IND)", "Indiana", "United States", "US", "America/Indiana/Indianapolis"),
    "Columbus, OH":       GeoLocation(39.9980, -82.8919, "Columbus (CMH)", "Ohio", "United States", "US", "America/New_York"),
    "Buffalo, NY":        GeoLocation(42.9405, -78.7322, "Buffalo (BUF)", "New York", "United States", "US", "America/New_York"),
    "Albany, NY":         GeoLocation(42.7482, -73.8020, "Albany (ALB)", "New York", "United States", "US", "America/New_York"),
}


class GeocodingError(RuntimeError):
    pass


def geocode(city: str, country_code: Optional[str] = None) -> GeoLocation:
    """
    Resolve a city name to geographic coordinates.

    For cities in _KALSHI_STATIONS, returns the hardcoded NOAA ASOS station
    coordinates that Kalshi uses for market settlement, bypassing the API.
    For all other cities, falls back to the Open-Meteo geocoding API.

    Args:
        city:         Free-text city name, e.g. "New York" or "New York, NY".
                      A trailing US state abbreviation (", NY") is stripped
                      automatically since Open-Meteo doesn't parse that format.
        country_code: Optional ISO-3166 alpha-2 code to narrow results, e.g. "US".

    Returns:
        GeoLocation with latitude, longitude, and metadata.

    Raises:
        GeocodingError: If no results are found or the API is unreachable.
    """
    # Use the exact Kalshi measurement station if known — avoids city-centroid drift.
    if city in _KALSHI_STATIONS:
        return _KALSHI_STATIONS[city]

    import re
    # Open-Meteo geocoding doesn't handle "City, ST" — strip trailing state code.
    search_name = re.sub(r",\s*[A-Z]{2}$", "", city).strip()
    params: dict = {"name": search_name, "count": 5, "language": "en", "format": "json"}
    try:
        resp = requests.get(_GEOCODE_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise GeocodingError(f"Geocoding request failed: {exc}") from exc

    data = resp.json()
    results = data.get("results", [])
    if not results:
        raise GeocodingError(f"No geocoding results for '{city}'.")

    # Prefer results matching country_code when supplied
    if country_code:
        cc = country_code.upper()
        filtered = [r for r in results if r.get("country_code", "").upper() == cc]
        if filtered:
            results = filtered

    hit = results[0]
    return GeoLocation(
        latitude=hit["latitude"],
        longitude=hit["longitude"],
        name=hit.get("name", city),
        region=hit.get("admin1", ""),
        country=hit.get("country", ""),
        country_code=hit.get("country_code", ""),
        timezone=hit.get("timezone", "UTC"),
    )
