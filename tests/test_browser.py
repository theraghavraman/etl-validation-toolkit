"""Ensure the browser's SQLite demonstration uses the production validation core."""

import json
import csv
import io

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
    with pytest.raises(ValueError, match="fewer fields than its header"):
        validate_csv("customer_id,full_name\nC001\n", "Customer")
    with pytest.raises(ValueError, match="Duplicate"):
        validate_csv("customer_id,customer_id\nC001,C002\n", "Customer")
    with pytest.raises(ValueError, match="header"):
        validate_csv("customer_id,\nC001,Alice\n", "Customer")


def test_browser_reject_download_escapes_formulas_but_preserves_json(example_dir):
    text = (example_dir / "data" / "customers_messy.csv").read_text()
    text = text.replace("bad-at-email", "=1+1")
    result = json.loads(validate_csv(text, "Customer"))
    raw = next(row for row in result["rejected_rows"] if row["field"] == "email")
    exported = next(row for row in csv.DictReader(io.StringIO(result["rejects_csv"]))
                    if row["field"] == "email")
    assert raw["raw_value"] == "=1+1"
    assert exported["raw_value"] == "'=1+1"


def test_browser_counts_blank_records_and_preserves_multiline_cells(example_dir):
    text = (example_dir / "data" / "customers_clean.csv").read_text()
    text = text.replace("Amina Khan", '"Amina\nKhan"') + "\n"
    result = json.loads(validate_csv(text))
    assert (result["rows_read"], result["rows_valid"], result["rows_rejected"]) == (4, 3, 1)
    assert result["valid_rows"][0]["full_name"] == "Amina\nKhan"
    assert {row["source_row_number"] for row in result["rejected_rows"]} == {5}
