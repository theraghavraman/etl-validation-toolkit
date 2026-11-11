"""Ensure the browser's SQLite demonstration uses the production validation core."""

import json

from etlguard.browser_demo import validate_csv


def test_browser_customer_feed_and_sqlite(example_dir):
    text = (example_dir / "data" / "customers_messy.csv").read_text()
    result = json.loads(validate_csv(text, "Customer"))
    assert (result["rows_read"], result["rows_valid"], result["rows_rejected"]) == (8, 4, 3)
    assert result["rows_loaded_sqlite"] == result["rows_valid"]
    assert len(result["rejected_rows"]) == 3
    assert "raw_row_json" in result["rejects_csv"]


def test_browser_transaction_debit_rule(example_dir):
    text = (example_dir / "data" / "transactions_messy.csv").read_text()
    result = json.loads(validate_csv(text, "Transaction"))
    assert result["rows_valid"] == 3
    assert result["rows_rejected"] == 2
    assert result["duplicates_dropped"] == 1


def test_browser_invalid_csv_headers():
    import pytest

    with pytest.raises(ValueError, match="header"):
        validate_csv("", "Customer")
    with pytest.raises(ValueError, match="Unknown"):
        validate_csv("data\n", "Unknown")
    with pytest.raises(ValueError, match="more fields than its header"):
        validate_csv("customer_id,full_name\nC001,Alice,extra\n", "Customer")
