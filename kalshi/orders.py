"""
Kalshi orders API wrapper.

Relevant endpoints:
  POST   /portfolio/orders          - place a single order
  POST   /portfolio/orders/batched  - place up to 20 orders atomically
  DELETE /portfolio/orders/{order_id} - cancel an order
  GET    /portfolio/orders          - list your open orders
  GET    /portfolio/orders/{order_id} - get a single order
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import requests


OrderSide = Literal["yes", "no"]
OrderType = Literal["limit", "market"]
OrderAction = Literal["buy", "sell"]


class OrdersAPI:
    """Thin wrapper around the Kalshi /portfolio/orders endpoints."""

    def __init__(self, session, base_url: str) -> None:
        self._session = session  # requests.Session with auth baked in
        self._base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raise(self, resp: requests.Response) -> None:
        if not resp.ok:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise requests.HTTPError(
                f"{resp.status_code} {resp.reason} for {resp.url} — {detail}",
                response=resp,
            )

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self._base_url}{path}"
        resp = self._session.get(url, params=params)
        self._raise(resp)
        return resp.json()

    def _post(self, path: str, body: dict) -> Any:
        url = f"{self._base_url}{path}"
        resp = self._session.post(url, json=body)
        self._raise(resp)
        return resp.json()

    def _delete(self, path: str) -> Any:
        url = f"{self._base_url}{path}"
        resp = self._session.delete(url)
        self._raise(resp)
        return resp.json()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def place_order(
        self,
        ticker: str,
        side: OrderSide,
        action: OrderAction,
        order_type: OrderType,
        count: int,
        yes_price: Optional[int] = None,
        no_price: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> dict:
        """
        Place a single order.

        Kalshi prices are in cents (1-99). For a limit order you must supply
        either yes_price or no_price (they are equivalent; Kalshi accepts
        either and converts internally).

        Args:
            ticker:          Market ticker, e.g. "HIGHNY-24DEC25-T40".
            side:            "yes" or "no" — which side of the contract.
            action:          "buy" or "sell".
            order_type:      "limit" or "market".
            count:           Number of contracts.
            yes_price:       Limit price in cents for the YES side (1-99).
            no_price:        Limit price in cents for the NO side (1-99).
            client_order_id: Optional idempotency key you generate.

        Returns:
            API response dict containing the created order.
        """
        body: dict[str, Any] = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "type": order_type,
            "count": count,
        }
        if yes_price is not None:
            body["yes_price"] = yes_price
        if no_price is not None:
            body["no_price"] = no_price
        if client_order_id is not None:
            body["client_order_id"] = client_order_id

        return self._post("/portfolio/orders", body)

    def place_batch_orders(self, orders: list[dict]) -> dict:
        """
        Place up to 20 orders in a single atomic request.

        Each element of `orders` should be a dict with the same fields as
        accepted by place_order (ticker, side, action, type, count, etc.).

        Returns:
            API response dict with a list of created orders.
        """
        if len(orders) > 20:
            raise ValueError("Kalshi batch orders are capped at 20 per request.")
        return self._post("/portfolio/orders/batched", {"orders": orders})

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order by its ID."""
        return self._delete(f"/portfolio/orders/{order_id}")

    def get_order(self, order_id: str) -> dict:
        """Fetch a single order by its ID."""
        return self._get(f"/portfolio/orders/{order_id}")

    def get_open_orders(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        """
        List your open orders, optionally filtered by market ticker.

        Returns:
            API response dict with "orders" list and optional "cursor".
        """
        params: dict[str, Any] = {"limit": limit, "status": "resting"}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        return self._get("/portfolio/orders", params=params)
