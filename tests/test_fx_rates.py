"""Tests for phase1_ingestion_parsing/fx_rates.py — mocked HTTP, no live calls
(the real contract was verified once by hand against both mirrors; see
PROGRESS.md's 2026-09-01 entry for that verification's own live output)."""

import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase1_ingestion_parsing.fx_rates import FXFetchError, fetch_live_rates  # noqa: E402

_SAMPLE_USD_RESPONSE = json.dumps(
    {"date": "2026-09-01", "usd": {"kes": 129.51486363, "ngn": 1338.15261871, "eur": 0.86237}}
).encode("utf-8")


def _mock_urlopen_success(*_args, **_kwargs):
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = _SAMPLE_USD_RESPONSE
    return cm


def test_fetch_live_rates_inverts_and_uppercases_correctly():
    with patch("urllib.request.urlopen", side_effect=_mock_urlopen_success):
        rates, as_of = fetch_live_rates("USD", ["KES", "NGN"])
    assert as_of == "2026-09-01"
    assert rates["KES"] == 1.0 / 129.51486363
    assert rates["NGN"] == 1.0 / 1338.15261871


def test_fetch_live_rates_omits_currency_the_source_does_not_have():
    with patch("urllib.request.urlopen", side_effect=_mock_urlopen_success):
        rates, _ = fetch_live_rates("USD", ["KES", "XYZ"])
    assert "KES" in rates
    assert "XYZ" not in rates  # never guessed


def test_fetch_live_rates_falls_back_to_second_mirror_on_first_failure():
    calls = {"n": 0}

    def side_effect(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("primary mirror unreachable")
        return _mock_urlopen_success()

    with patch("urllib.request.urlopen", side_effect=side_effect):
        rates, as_of = fetch_live_rates("USD", ["KES"])
    assert calls["n"] == 2
    assert rates["KES"] == 1.0 / 129.51486363


def test_fetch_live_rates_raises_fxfetcherror_when_both_mirrors_fail():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        try:
            fetch_live_rates("USD", ["KES"])
            assert False, "expected FXFetchError"
        except FXFetchError:
            pass


if __name__ == "__main__":
    test_fetch_live_rates_inverts_and_uppercases_correctly()
    test_fetch_live_rates_omits_currency_the_source_does_not_have()
    test_fetch_live_rates_falls_back_to_second_mirror_on_first_failure()
    test_fetch_live_rates_raises_fxfetcherror_when_both_mirrors_fail()
    print("fx_rates tests OK")
