"""
Kalshi API authentication.

Kalshi uses RSA-signed requests. Each HTTP request must include three headers:
  - KALSHI-ACCESS-KEY:       Your API key ID
  - KALSHI-ACCESS-SIGNATURE: Base64-encoded RSA-PSS signature of the message
  - KALSHI-ACCESS-TIMESTAMP: Unix timestamp in milliseconds (str)

The signed message is: f"{timestamp}{method}{path}"
  - timestamp: milliseconds since epoch (same value sent in header)
  - method:    uppercase HTTP verb, e.g. "GET"
  - path:      URL path + query string, e.g. "/trade-api/v2/markets?status=open"
"""

import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def load_private_key(pem_path: str):
    """Load an RSA private key from a PEM file."""
    key_bytes = Path(pem_path).read_bytes()
    return serialization.load_pem_private_key(key_bytes, password=None)


def build_auth_headers(
    api_key_id: str,
    private_key,
    method: str,
    path: str,
) -> dict[str, str]:
    """
    Return the three Kalshi authentication headers for a single request.

    Args:
        api_key_id:  Your Kalshi API key ID.
        private_key: Loaded RSA private key object (from load_private_key).
        method:      HTTP method in uppercase ("GET", "POST", etc.).
        path:        Full URL path including query string,
                     e.g. "/trade-api/v2/markets?status=open".

    Returns:
        Dict of headers ready to merge into a requests.Session headers dict.
    """
    timestamp_ms = str(int(time.time() * 1000))
    message = f"{timestamp_ms}{method.upper()}{path}"

    signature_bytes = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    signature_b64 = base64.b64encode(signature_bytes).decode("utf-8")

    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": signature_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
    }
