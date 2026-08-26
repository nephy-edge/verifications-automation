"""Tests for Phase 1 cashmap.py — cash-map parsing and account matching."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase1_ingestion_parsing.cashmap import match_accounts, parse_cash_map  # noqa: E402


def test_free_text_one_account_per_line_is_parsed(tmp_path):
    # Leasy-style: each "Account N:" answer on its own line, with the
    # question's own bracketed template label preceding the real answer.
    text = (
        "Borrower name: Leasy\n"
        "4.2 For each account, describe its purpose, bank, and who has access\n"
        "  [Account 1: Purpose - Loan disbursements] Account 1: Collection account\n"
        "Account 2: Issuance account to receive disbursements from Lendable\n"
        "Account 3: Operating account\n"
        "\n"
        "4.3 Next question\n"
    )
    p = tmp_path / "cashmap.txt"
    p.write_text(text, encoding="utf-8")
    parsed = parse_cash_map(p)
    assert parsed["borrower_name"] == "Leasy"
    assert [a["id"] for a in parsed["accounts"]] == ["account_1", "account_2", "account_3"]
    assert parsed["accounts"][0]["description"] == "Collection account"
    assert all(a["bank_name"] == "" for a in parsed["accounts"])  # no bank named anywhere


def test_free_text_accounts_run_together_in_one_paragraph(tmp_path):
    # First Circle-style: all three answers concatenated in one paragraph,
    # with bank names inline.
    text = (
        "Borrower name: First Circle\n"
        "4.2 For each account, describe its purpose, bank, and who has access\n"
        "    Account 1: Loan disbursement account at BDO Philippines. "
        "Account 2: Designated collections account at Union Bank. "
        "Account 3: Operational account for payroll.\n"
        "4.3 Next question\n"
    )
    p = tmp_path / "cashmap.txt"
    p.write_text(text, encoding="utf-8")
    parsed = parse_cash_map(p)
    accounts = {a["id"]: a for a in parsed["accounts"]}
    assert accounts["account_1"]["bank_name"] == "BDO Philippines"
    assert accounts["account_2"]["bank_name"] == "Union Bank"
    assert accounts["account_3"]["bank_name"] == ""


def test_structured_json_schema_is_parsed_directly(tmp_path):
    data = {
        "narrative": {"borrower_name": "Thai Lending Co., Ltd."},
        "accounts": [
            {
                "id": "ACCT-0001",
                "label": "Disbursement account",
                "bank_name": "Kasikornbank",
                "currency": "THB",
                "purpose": ["receives_from_lender"],
            }
        ],
    }
    p = tmp_path / "cashmap.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    parsed = parse_cash_map(p)
    assert parsed["source"] == "structured_json"
    assert parsed["borrower_name"] == "Thai Lending Co., Ltd."
    assert parsed["accounts"][0]["bank_name"] == "Kasikornbank"
    assert parsed["accounts"][0]["currency"] == "THB"


def test_match_narrows_to_the_named_bank_when_present():
    accounts = [
        {"id": "a1", "bank_name": "BDO Philippines"},
        {"id": "a2", "bank_name": "Union Bank"},
    ]
    result = match_accounts("BDO PHILIPPINES account statement", "stmt.pdf", accounts)
    assert [a["id"] for a in result] == ["a1"]


def test_match_returns_all_accounts_when_none_are_named():
    # A cash map with no bank names at all (e.g. Leasy) can't narrow -
    # every account is an equally valid candidate for a human to pick.
    accounts = [{"id": "a1", "bank_name": ""}, {"id": "a2", "bank_name": ""}]
    result = match_accounts("anything", "anything.pdf", accounts)
    assert [a["id"] for a in result] == ["a1", "a2"]


def test_match_returns_all_accounts_when_named_bank_does_not_appear():
    accounts = [{"id": "a1", "bank_name": "BDO Philippines"}, {"id": "a2", "bank_name": "Union Bank"}]
    result = match_accounts("some unrelated statement text", "unrelated.csv", accounts)
    assert [a["id"] for a in result] == ["a1", "a2"]


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        test_free_text_one_account_per_line_is_parsed(Path(d))
        test_free_text_accounts_run_together_in_one_paragraph(Path(d))
        test_structured_json_schema_is_parsed_directly(Path(d))
    test_match_narrows_to_the_named_bank_when_present()
    test_match_returns_all_accounts_when_none_are_named()
    test_match_returns_all_accounts_when_named_bank_does_not_appear()
    print("cashmap tests OK")
