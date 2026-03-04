import os
from dotenv import load_dotenv

load_dotenv()

# ── Kalshi API credentials ────────────────────────────────────────────────────
# Your API key ID is found in the Kalshi web app under Settings > API Keys.
KALSHI_API_KEY_ID: str = os.getenv("KALSHI_API_KEY_ID", "YOUR_API_KEY_ID_HERE")

# Path to your RSA private key file (.pem). Generate a key pair in the
# Kalshi web app and save the downloaded private key at this path.
KALSHI_PRIVATE_KEY_PATH: str = os.getenv(
    "KALSHI_PRIVATE_KEY_PATH", "kalshi_private_key.pem"
)

# ── Anthropic API key ─────────────────────────────────────────────────────────
# Used by the Claude analyst. Get yours at console.anthropic.com.
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_API_KEY_HERE")

# ── API endpoints ─────────────────────────────────────────────────────────────
BASE_URL: str = "https://api.elections.kalshi.com/trade-api/v2"

# ── Trading parameters ────────────────────────────────────────────────────────
# Minimum edge (our probability minus market price) to surface a recommendation.
EDGE_THRESHOLD: float = float(os.getenv("EDGE_THRESHOLD", "0.15"))

# Maximum contracts per order (hard cap regardless of Kelly sizing).
MAX_CONTRACTS: int = int(os.getenv("MAX_CONTRACTS", "25"))

# Maximum dollars at risk per individual trade.
MAX_TRADE_RISK_DOLLARS: float = float(os.getenv("MAX_TRADE_RISK_DOLLARS", "100.0"))

# ── Portfolio tracker ──────────────────────────────────────────────────────
# Path to the SQLite database used by the portfolio tracker.
DB_PATH: str = os.getenv("DB_PATH", "trades.db")
