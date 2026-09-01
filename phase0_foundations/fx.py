"""Static, config-driven FX normalization (SOP 1 gap: mixed-currency sums).

No live rate lookup — rates are a version-controlled table in config.yaml,
the same discipline as the B3 thresholds. An empty rates table (today's
default) converts nothing, matching pre-FX behavior exactly; a currency
present on a row but absent from the table is left unconverted and its code
surfaced as an "unmapped" warning to the caller, instead of guessing a rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FXConfig:
    base_currency: str = "USD"
    rates: dict[str, float] = field(default_factory=dict)  # {"KES": 0.0067, ...} = units of base_currency per 1 unit of that currency

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "FXConfig":
        d = d or {}
        return cls(
            base_currency=str(d.get("base_currency") or "USD").strip().upper(),
            rates={str(k).strip().upper(): float(v) for k, v in (d.get("rates") or {}).items()},
        )


def convert_to_base(amount: float, currency: str, fx: FXConfig) -> tuple[float, str | None]:
    """Convert ``amount`` (in ``currency``) to ``fx.base_currency``.

    Returns ``(converted_amount, unmapped_currency_code)``. A blank currency
    or one matching the base currency is assumed already-base (no
    conversion, no warning) — most current sources are single-currency and
    never populate this field. A currency present but absent from
    ``fx.rates`` is left unconverted, with its code returned as the second
    element so callers can surface it instead of silently mis-summing.
    """
    code = (currency or "").strip().upper()
    if not code or code == fx.base_currency:
        return amount, None
    rate = fx.rates.get(code)
    if rate is None:
        return amount, code
    return amount * rate, None
