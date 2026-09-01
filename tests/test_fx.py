"""Tests for phase0_foundations/fx.py — plain asserts, runnable with the venv python."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations.fx import FXConfig, convert_to_base  # noqa: E402


def test_from_dict_defaults_to_usd_and_empty_rates():
    fx = FXConfig.from_dict(None)
    assert fx.base_currency == "USD"
    assert fx.rates == {}


def test_from_dict_normalizes_currency_codes_to_uppercase():
    fx = FXConfig.from_dict({"base_currency": "usd", "rates": {"kes": 0.0067}})
    assert fx.base_currency == "USD"
    assert fx.rates == {"KES": 0.0067}


def test_convert_blank_currency_is_assumed_base_no_warning():
    fx = FXConfig(base_currency="USD", rates={"KES": 0.0067})
    amount, unmapped = convert_to_base(100.0, "", fx)
    assert amount == 100.0
    assert unmapped is None


def test_convert_matching_base_currency_is_a_no_op():
    fx = FXConfig(base_currency="USD", rates={"KES": 0.0067})
    amount, unmapped = convert_to_base(100.0, "usd", fx)
    assert amount == 100.0
    assert unmapped is None


def test_convert_known_currency_applies_rate():
    fx = FXConfig(base_currency="USD", rates={"KES": 0.0067})
    amount, unmapped = convert_to_base(1000.0, "KES", fx)
    assert amount == 6.7
    assert unmapped is None


def test_convert_unknown_currency_left_unconverted_and_flagged():
    fx = FXConfig(base_currency="USD", rates={"KES": 0.0067})
    amount, unmapped = convert_to_base(500.0, "NGN", fx)
    assert amount == 500.0
    assert unmapped == "NGN"


if __name__ == "__main__":
    test_from_dict_defaults_to_usd_and_empty_rates()
    test_from_dict_normalizes_currency_codes_to_uppercase()
    test_convert_blank_currency_is_assumed_base_no_warning()
    test_convert_matching_base_currency_is_a_no_op()
    test_convert_known_currency_applies_rate()
    test_convert_unknown_currency_left_unconverted_and_flagged()
    print("fx tests OK")
