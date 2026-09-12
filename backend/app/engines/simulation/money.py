"""Money formatting for management summaries.

Engines never round money internally (DESIGN_CONTRACT §4); this helper only
renders a float for a human sentence such as "revenue at risk ₹4.2 L → ₹6.1 L".
The default currency is INR with Indian lakh / crore grouping; other
currencies fall back to thousand / million suffixes with a symbol when known.
"""

from __future__ import annotations

_SYMBOLS: dict[str, str] = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}

LAKH = 100_000.0
CRORE = 10_000_000.0
THOUSAND = 1_000.0
MILLION = 1_000_000.0
BILLION = 1_000_000_000.0


def _trim(value: float, digits: int = 2) -> str:
    """``4.20`` → ``"4.2"``, ``4.00`` → ``"4"``; keeps up to ``digits`` decimals."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def format_money(amount: float | None, currency: str = "INR") -> str:
    """Compact human rendering: ``₹4.2 L``, ``₹1.05 Cr``, ``$3.4M``, ``EUR 950``."""
    if amount is None:
        return "n/a"
    code = (currency or "INR").upper()
    symbol = _SYMBOLS.get(code)
    prefix = symbol if symbol is not None else f"{code} "
    sign = "-" if amount < 0 else ""
    value = abs(float(amount))
    if code == "INR":
        if value >= CRORE:
            return f"{sign}{prefix}{_trim(value / CRORE)} Cr"
        if value >= LAKH:
            return f"{sign}{prefix}{_trim(value / LAKH)} L"
        return f"{sign}{prefix}{value:,.0f}"
    if value >= BILLION:
        return f"{sign}{prefix}{_trim(value / BILLION)}B"
    if value >= MILLION:
        return f"{sign}{prefix}{_trim(value / MILLION)}M"
    if value >= THOUSAND * 10:
        return f"{sign}{prefix}{_trim(value / THOUSAND, 1)}K"
    return f"{sign}{prefix}{value:,.0f}"


__all__ = ["format_money"]
