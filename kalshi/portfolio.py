"""
Kalshi portfolio endpoints.

Relevant endpoints:
  GET /portfolio/balance    — cash balance
  GET /portfolio/positions  — open market positions (paginated)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)


class PortfolioAPI:
    """Wrapper around Kalshi /portfolio endpoints."""

    def __init__(self, session, base_url: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self._base_url}{path}"
        resp = self._session.get(url, params=params)
        if not resp.ok:
            # Include the response body so Kalshi's error message is visible
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise requests.HTTPError(
                f"{resp.status_code} {resp.reason} for {url} — {detail}",
                response=resp,
            )
        return resp.json()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_balance(self) -> float:
        """
        Return available cash balance in dollars.

        Kalshi returns balance as an integer in cents. If the response
        structure differs from expected, logs a warning and returns 0.
        """
        data = self._get("/portfolio/balance")

        # The Kalshi API may nest balance under different keys depending on version.
        # Try common patterns.
        raw = (
            data.get("balance")
            or data.get("available_balance")
            or data.get("cash_balance")
            or 0
        )
        try:
            cents = int(raw)
        except (TypeError, ValueError):
            log.warning("Unexpected balance value %r — defaulting to 0", raw)
            return 0.0

        return cents / 100  # cents → dollars

    def get_positions(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Fetch one page of market positions.

        Returns the raw API dict containing "market_positions" and "cursor".
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if ticker:
            params["ticker"] = ticker
        return self._get("/portfolio/positions", params=params)

    def get_all_positions(self) -> list[dict]:
        """
        Paginate through all open market positions.

        Each position dict contains at minimum:
          ticker, position (net contracts), market_exposure, value (cents).
        """
        positions: list[dict] = []
        cursor: Optional[str] = None

        while True:
            resp = self.get_positions(limit=100, cursor=cursor)
            page = resp.get("market_positions", [])
            positions.extend(page)
            cursor = resp.get("cursor")
            if not cursor or len(page) < 100:
                break

        # Filter to positions with non-zero net contracts
        return [p for p in positions if p.get("position", 0) != 0]
