"""
Kalshi markets API wrapper.

Relevant endpoints:
  GET /markets          - list/filter markets
  GET /markets/{ticker} - single market by ticker
  GET /events           - list events (each event groups related markets)
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

import requests


class MarketsAPI:
    """Thin wrapper around the Kalshi /markets and /events endpoints."""

    def __init__(self, session, base_url: str) -> None:
        self._session = session  # requests.Session with auth baked in
        self._base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self._base_url}{path}"
        response = self._session.get(url, params=params)
        if not response.ok:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise requests.HTTPError(
                f"{response.status_code} {response.reason} for {url} — {detail}",
                response=response,
            )
        return response.json()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_markets(
        self,
        status: str = "open",
        limit: int = 100,
        cursor: Optional[str] = None,
        ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        series_ticker: Optional[str] = None,
    ) -> dict:
        """
        Fetch a page of markets.

        Args:
            status:        "open" | "unopened" | "closed" | "settled"
            limit:         Results per page (1-1000).
            cursor:        Pagination cursor from a previous response.
            ticker:        Filter to a specific market ticker.
            event_ticker:  Filter to markets under a specific event.
            series_ticker: Filter to markets under a specific series.

        Returns:
            Raw API response dict with keys "markets" and "cursor".
        """
        params: dict[str, Any] = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if ticker:
            params["ticker"] = ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker

        return self._get("/markets", params=params)

    def get_market(self, ticker: str) -> dict:
        """Fetch a single market by its ticker."""
        return self._get(f"/markets/{ticker}")

    def get_weather_markets(
        self,
        series_ticker: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Fetch all open weather-related markets.

        Kalshi weather markets are grouped under series with tickers like
        "HIGHNY" (NY high temp), "HIGHLAX" (LA high temp), etc.
        Pass a series_ticker to narrow results to a specific location/series.

        Returns:
            List of market dicts from the API.
        """
        markets: list[dict] = []
        cursor: Optional[str] = None

        while True:
            resp = self.get_markets(
                status="open",
                limit=limit,
                cursor=cursor,
                series_ticker=series_ticker,
            )
            page = resp.get("markets", [])
            markets.extend(page)
            cursor = resp.get("cursor")
            # Stop when there are no more pages or fewer results than requested
            if not cursor or len(page) < limit:
                break

        return markets

    def get_events(
        self,
        status: str = "open",
        series_ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        """
        Fetch a page of events.

        Each Kalshi event groups one or more related markets (e.g. all the
        yes/no brackets for "NYC high temperature on date X").
        """
        params: dict[str, Any] = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker

        return self._get("/events", params=params)
