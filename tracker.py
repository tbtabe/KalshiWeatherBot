"""
Portfolio tracker — SQLite-backed trade journal.

Records every placed order and resolves outcomes by querying the Kalshi API
for settled markets.  Provides summary statistics for --review mode.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Market identity
    ticker                TEXT NOT NULL,
    series                TEXT,
    city                  TEXT,
    market_date           TEXT,
    threshold_f           REAL,
    condition             TEXT,
    -- Trade details
    side                  TEXT,
    contracts             INTEGER,
    entry_price_cents     INTEGER,
    cost_dollars          REAL,
    max_gain_dollars      REAL,
    expected_value_dollars REAL,
    edge                  REAL,
    -- Probability estimates
    stat_probability      REAL,
    claude_probability    REAL,
    confidence            TEXT,
    reasoning             TEXT,
    -- Metadata
    placed_at             TEXT,
    order_id              TEXT,
    -- Resolution (filled in later)
    outcome               TEXT DEFAULT 'pending',
    resolved_at           TEXT,
    pnl_dollars           REAL
)
"""


class TradeTracker:
    """
    Persists trade history in a local SQLite database.

    Usage:
        tracker = TradeTracker("trades.db")
        tracker.record_trade(rec, order)   # call after a successful order
        tracker.resolve_pending(client)    # call to settle outstanding trades
        summary = tracker.get_summary()
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._init_db()

    # ── Internal helpers ──────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(_CREATE_TABLE)

    # ── Write ─────────────────────────────────────────────────────────────

    def record_trade(self, rec, order: dict) -> int:
        """
        Persist a placed trade.

        Args:
            rec:   TradeRecommendation from analyzer
            order: Raw Kalshi API response from place_order()

        Returns:
            The new row ID.
        """
        order_id = order.get("order", {}).get("order_id", "")
        placed_at = datetime.now(timezone.utc).isoformat()

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO trades (
                    ticker, series, city, market_date, threshold_f, condition,
                    side, contracts, entry_price_cents,
                    cost_dollars, max_gain_dollars, expected_value_dollars,
                    edge, stat_probability, claude_probability,
                    confidence, reasoning,
                    placed_at, order_id
                ) VALUES (
                    ?,?,?,?,?,?,
                    ?,?,?,
                    ?,?,?,
                    ?,?,?,
                    ?,?,
                    ?,?
                )
                """,
                (
                    rec.spec.ticker,
                    rec.spec.series,
                    rec.spec.display_name,
                    str(rec.spec.date),
                    rec.spec.threshold_f,
                    rec.spec.condition,
                    rec.side,
                    rec.suggested_contracts,
                    rec.market_price_cents,
                    rec.risk_dollars,
                    rec.max_gain_dollars,
                    rec.expected_value_dollars,
                    rec.edge,
                    rec.stat_probability,
                    rec.claude_probability,
                    rec.confidence,
                    rec.reasoning,
                    placed_at,
                    order_id,
                ),
            )
            return cur.lastrowid

    # ── Resolution ────────────────────────────────────────────────────────

    def resolve_pending(self, client) -> int:
        """
        Query the Kalshi API for all pending trades and update outcomes.

        A market is "settled" when its status is "settled" and has a
        result.  Kalshi returns yes_price == 100 (YES wins) or 0 (NO wins)
        on settled markets.

        Args:
            client: KalshiClient instance.

        Returns:
            Number of trades newly resolved.
        """
        with self._conn() as conn:
            pending = conn.execute(
                "SELECT id, ticker, side, contracts, entry_price_cents, cost_dollars, max_gain_dollars "
                "FROM trades WHERE outcome = 'pending'"
            ).fetchall()

        resolved = 0
        for row in pending:
            try:
                mkt = client.markets.get_market(row["ticker"])
            except Exception:
                continue

            market = mkt.get("market", mkt)  # API may nest under "market"
            status = market.get("status", "")
            if status != "settled":
                continue

            # Determine winner from settled yes_price (100 = YES won, 0 = NO won)
            settled_yes = market.get("yes_price", None)
            if settled_yes is None:
                continue

            yes_won = settled_yes == 100
            side = row["side"]

            if (side == "yes" and yes_won) or (side == "no" and not yes_won):
                outcome = "win"
                pnl = row["max_gain_dollars"] - row["cost_dollars"]
            else:
                outcome = "loss"
                pnl = -row["cost_dollars"]

            resolved_at = datetime.now(timezone.utc).isoformat()
            with self._conn() as conn:
                conn.execute(
                    "UPDATE trades SET outcome=?, resolved_at=?, pnl_dollars=? WHERE id=?",
                    (outcome, resolved_at, pnl, row["id"]),
                )
            resolved += 1

        return resolved

    # ── Query ─────────────────────────────────────────────────────────────

    def get_trades(self, outcome: Optional[str] = None) -> list[sqlite3.Row]:
        """Return trades, optionally filtered by outcome ('win'/'loss'/'pending')."""
        with self._conn() as conn:
            if outcome:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE outcome=? ORDER BY placed_at DESC",
                    (outcome,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY placed_at DESC"
                ).fetchall()
        return rows

    def get_summary(self) -> dict:
        """
        Return aggregate P&L statistics.

        Returns a dict with keys:
            total_trades, wins, losses, pending,
            win_rate, total_cost, total_pnl, roi,
            by_confidence: dict[str -> dict]
        """
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM trades").fetchall()

        settled = [r for r in rows if r["outcome"] != "pending"]
        wins    = [r for r in settled if r["outcome"] == "win"]
        losses  = [r for r in settled if r["outcome"] == "loss"]
        pending = [r for r in rows if r["outcome"] == "pending"]

        total_cost = sum(r["cost_dollars"] or 0 for r in settled)
        total_pnl  = sum(r["pnl_dollars"] or 0 for r in settled)

        by_confidence: dict[str, dict] = {}
        for row in settled:
            conf = (row["confidence"] or "unknown").lower()
            bucket = by_confidence.setdefault(
                conf, {"wins": 0, "losses": 0, "pnl": 0.0}
            )
            if row["outcome"] == "win":
                bucket["wins"] += 1
            else:
                bucket["losses"] += 1
            bucket["pnl"] += row["pnl_dollars"] or 0

        return {
            "total_trades": len(rows),
            "wins":         len(wins),
            "losses":       len(losses),
            "pending":      len(pending),
            "win_rate":     len(wins) / len(settled) if settled else None,
            "total_cost":   total_cost,
            "total_pnl":    total_pnl,
            "roi":          total_pnl / total_cost if total_cost else None,
            "by_confidence": by_confidence,
        }
