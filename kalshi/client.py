"""
KalshiClient — top-level entry point that wires together auth, markets, and orders.

Usage:
    from kalshi import KalshiClient

    client = KalshiClient(
        api_key_id="your-key-id",
        private_key_path="kalshi_private_key.pem",
    )
    markets = client.markets.get_weather_markets(series_ticker="HIGHNY")
"""

from __future__ import annotations

from urllib.parse import urlparse

import requests

from .auth import build_auth_headers, load_private_key
from .markets import MarketsAPI
from .orders import OrdersAPI
from .portfolio import PortfolioAPI


class _AuthenticatedSession(requests.Session):
    """
    requests.Session subclass that injects Kalshi auth headers on every request.

    The headers must be recomputed per-request because the timestamp and
    signature change each time.
    """

    def __init__(self, api_key_id: str, private_key, base_url: str) -> None:
        super().__init__()
        self._api_key_id = api_key_id
        self._private_key = private_key
        self._base_path = urlparse(base_url).path  # e.g. "/trade-api/v2"
        self.headers.update({"Content-Type": "application/json"})

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        # Kalshi verifies the signature against the bare path only —
        # query parameters must NOT be included in the signed message.
        path = urlparse(url).path

        auth_headers = build_auth_headers(
            api_key_id=self._api_key_id,
            private_key=self._private_key,
            method=method,
            path=path,
        )
        # Merge auth headers without mutating the session-level headers permanently
        existing = kwargs.get("headers", {})
        kwargs["headers"] = {**existing, **auth_headers}
        return super().request(method, url, **kwargs)


class KalshiClient:
    """
    Authenticated Kalshi API client.

    Attributes:
        markets:   MarketsAPI   — fetch and filter markets.
        orders:    OrdersAPI    — place, cancel, and query orders.
        portfolio: PortfolioAPI — balance and positions.
    """

    def __init__(
        self,
        api_key_id: str,
        private_key_path: str,
        base_url: str = "https://api.elections.kalshi.com/trade-api/v2",
    ) -> None:
        private_key = load_private_key(private_key_path)
        session = _AuthenticatedSession(
            api_key_id=api_key_id,
            private_key=private_key,
            base_url=base_url,
        )
        self.markets   = MarketsAPI(session=session, base_url=base_url)
        self.orders    = OrdersAPI(session=session, base_url=base_url)
        self.portfolio = PortfolioAPI(session=session, base_url=base_url)

    @classmethod
    def from_config(cls) -> "KalshiClient":
        """Construct a client from settings in config.py."""
        from config import BASE_URL, KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PATH

        return cls(
            api_key_id=KALSHI_API_KEY_ID,
            private_key_path=KALSHI_PRIVATE_KEY_PATH,
            base_url=BASE_URL,
        )
