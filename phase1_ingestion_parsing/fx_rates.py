"""Live FX-rate lookup — fawazahmed0/currency-api (MIT-licensed, open source,
free, no API key). A confirmed-live client, not a guess: the real contract
(one JSON file per base currency, `{"date": ..., "<base>": {"<ccy>": rate}}`,
rate = units of `<ccy>` per 1 unit of `<base>`) was verified by calling both
mirrors directly — including the African currencies this project actually
needs (KES, NGN, GHS, RWF), which the more commonly-cited ECB-based Frankfurter
API does not cover at all — before wiring this in, the same discipline as
`vehicle_verify.py`'s confirmed-only country contracts.

Primary + fallback are the same static, daily-updated dataset served from two
independent CDNs; no account, no token, no rate limit encountered in testing.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterable

PRIMARY_URL = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base}.json"
FALLBACK_URL = "https://latest.currency-api.pages.dev/v1/currencies/{base}.json"


class FXFetchError(RuntimeError):
    """Raised when neither mirror could be reached or parsed — never a partial guess."""


def fetch_live_rates(
    base_currency: str, currencies: Iterable[str], timeout: float = 10.0
) -> tuple[dict[str, float], str | None]:
    """Live rate for each of ``currencies``, as units of ``base_currency`` per
    1 unit of that currency — the same convention `FXConfig.rates` uses.

    Returns ``(rates, as_of_date)``. ``rates`` only contains currencies the
    source actually returned; the same "don't guess a missing one" discipline
    as the static table — a currency absent here still surfaces via the
    caller's existing `unmapped_currencies` reporting. Raises `FXFetchError`
    if both mirrors fail (network down, bad response), so a caller can fall
    back to config.yaml's static table instead of running on a corrupt guess.
    """
    base = base_currency.strip().lower()
    data: dict | None = None
    for url in (PRIMARY_URL.format(base=base), FALLBACK_URL.format(base=base)):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except Exception:  # noqa: BLE001 - any failure tries the next mirror, then raises below
            continue
    if data is None:
        raise FXFetchError(f"Could not reach either currency-api mirror for base '{base_currency}'.")

    base_rates = data.get(base) or {}
    as_of = data.get("date")
    rates: dict[str, float] = {}
    for code in currencies:
        value = base_rates.get(code.strip().lower())
        if value:
            rates[code.strip().upper()] = 1.0 / value
    return rates, as_of
