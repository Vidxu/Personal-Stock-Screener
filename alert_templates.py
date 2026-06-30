"""
Reusable alert message templates for Telegram delivery.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlertTemplate:
    title: str
    body: str
    sound_seconds: float = 2.5

    def render(self, **kwargs: object) -> "RenderedAlert":
        return RenderedAlert(
            title=self.title.format(**kwargs),
            message=self.body.format(**kwargs),
            sound_seconds=self.sound_seconds,
        )


@dataclass(frozen=True)
class RenderedAlert:
    title: str
    message: str
    sound_seconds: float = 2.5


def _inr(value: float | int | str | None) -> str:
    if value is None or value == "":
        return "?"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if n == int(n):
        return f"₹{int(n):,}"
    return f"₹{n:,.2f}"


# ── OR + Prev Day High Breakout ───────────────────────────────────────────────

OR_BREAKOUT_CROSS = AlertTemplate(
    title="OR Breakout · {symbol}",
    body=(
        "{symbol} crossed OR high {or_high} and prev-day high {pd_high}. "
        "LTP {price} ({pct_change})"
    ),
    sound_seconds=2.5,
)


def or_breakout_alert(symbol: str, meta: dict) -> RenderedAlert:
    """Build a Telegram alert for a fresh OR + prev-day-high crossover."""
    return OR_BREAKOUT_CROSS.render(
        symbol=symbol,
        or_high=_inr(meta.get("_or_high")),
        pd_high=_inr(meta.get("_pd_high")),
        price=_inr(meta.get("Price")),
        pct_change=meta.get("% change", "—"),
    )
