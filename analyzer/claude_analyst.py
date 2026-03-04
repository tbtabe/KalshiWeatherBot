"""
Claude-powered market analysis.

Sends weather forecast data and market context to Claude Sonnet with adaptive
thinking and structured JSON output. Falls back to the statistical estimate
if the API call fails.

Model: claude-sonnet-4-6 with thinking: {type: "adaptive"}
Output: validated JSON via output_config.format (json_schema)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import anthropic

if TYPE_CHECKING:
    from .models import MarketSpec

log = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

_SYSTEM = """\
You are a quantitative analyst specializing in short-term weather prediction markets.
Given forecast data from NWS and Open-Meteo, assess whether a Kalshi binary market
is mispriced. Be precise and data-driven. Typical NWS 1-day RMSE for US city high
temperatures is 3–5°F; weight NWS slightly higher than Open-Meteo for US locations.
Consider active weather alerts as indicators of elevated uncertainty.

Kalshi settles temperature markets against the NWS Climatological Report (Daily)
for the specific NOAA ASOS station listed in each market's rules. The station name
is included in the market location below. Use "high" or "low" as specified.

Respond with a single JSON object matching this schema exactly — no markdown fences,
no extra keys:
{
  "probability_estimate": <number 0–1>,
  "confidence": <"high"|"medium"|"low">,
  "reasoning": <string, 2–3 sentences>,
  "recommend_trade": <true|false>
}"""


@dataclass
class MarketAnalysis:
    probability_estimate: float
    confidence: str           # "high" | "medium" | "low"
    reasoning: str
    recommend_trade: bool


class ClaudeAnalyst:
    """Wraps the Anthropic API for per-market weather analysis."""

    def __init__(self, api_key: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)

    def analyze(
        self,
        spec: Any,         # MarketSpec
        weather: Any,      # WeatherReport
        stat_prob: float,
        market: dict,
    ) -> MarketAnalysis:
        """
        Analyze a weather market and return Claude's probability estimate.

        Falls back to stat_prob with low confidence on any API failure.
        """
        try:
            return self._call(spec, weather, stat_prob, market)
        except Exception as exc:
            log.warning("Claude analysis failed for %s: %s", spec.ticker, exc)
            var_label = "low" if spec.variable == "low" else "high"
            return MarketAnalysis(
                probability_estimate=stat_prob,
                confidence="low",
                reasoning=(
                    f"Falling back to statistical estimate (Claude unavailable): "
                    f"P({var_label} {spec.condition} {spec.threshold_f:.0f}°F) = {stat_prob:.1%}."
                ),
                recommend_trade=True,
            )

    def _call(self, spec: Any, weather: Any, stat_prob: float, market: dict) -> MarketAnalysis:
        yes_ask = market.get("yes_ask", 50)

        # Build forecast lines
        fc_lines = []
        for fc in weather.forecasts:
            high = f"{fc.temp_high_f:.1f}°F" if fc.temp_high_f is not None else "N/A"
            low  = f"{fc.temp_low_f:.1f}°F"  if fc.temp_low_f  is not None else "N/A"
            prec = f"{fc.precip_probability:.0f}%" if fc.precip_probability is not None else "N/A"
            summ = f'"{fc.summary}"' if fc.summary else ""
            src  = "NWS" if fc.source == "nws" else "Open-Meteo"
            fc_lines.append(f"  {src:12s} high {high}, low {low}, precip {prec}  {summ}")

        alert_lines = [
            f"  [{a.severity}] {a.event}: {a.headline}"
            for a in weather.alerts
        ] or ["  None"]

        var_label = "low" if spec.variable == "low" else "high"

        if spec.condition == "between" and spec.upper_bound_f is not None:
            question = (
                f"between {spec.threshold_f:.1f}°F and {spec.upper_bound_f:.1f}°F"
            )
        else:
            question = f"{spec.condition} {spec.threshold_f:.0f}°F"

        prompt = (
            f"Market:   {spec.ticker}\n"
            f"Station:  {weather.city}\n"
            f"Question: Will the daily {var_label} temperature at this station be "
            f"{question} on {spec.date}?\n"
            f"YES price: {yes_ask}¢  (market-implied probability: {yes_ask}%)\n"
            f"\nForecast data:\n" + "\n".join(fc_lines) +
            f"\n\nActive alerts:\n" + "\n".join(alert_lines) +
            f"\n\nStatistical baseline: P(YES) = {stat_prob:.1%} "
            f"(normal distribution around forecast mean).\n"
            f"\nRespond with the JSON object only."
        )

        response = self._client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

        text_block = next((b for b in response.content if b.type == "text"), None)
        if text_block is None:
            raise ValueError("No text block in Claude response")

        # Strip optional markdown fences before parsing
        raw = text_block.text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(raw)
        prob = max(0.01, min(0.99, float(data["probability_estimate"])))

        return MarketAnalysis(
            probability_estimate=prob,
            confidence=data["confidence"],
            reasoning=data["reasoning"],
            recommend_trade=bool(data["recommend_trade"]),
        )
