from __future__ import annotations

import pytest

from etlguard.checks import DatasetChecker
from etlguard.config import ChecksSettings, CleaningSettings, ForeignKeySettings, RowCountSettings
from etlguard.core import ValidationEngine
from etlguard.examples.models import Customer, Transaction
from etlguard.validation import RowValidator


@pytest.mark.parametrize("checks", [
    ChecksSettings(unique=["customerid"]),
    ChecksSettings(max_null_pct={"emali": 0.1}),
    ChecksSettings(foreign_keys={"customerid": ForeignKeySettings(values=["C001"])}),
])
def test_misspelled_dataset_columns_fail_before_reading_any_rows(checks):
    from etlguard.config import ConfigError
    with pytest.raises(ConfigError, match="unknown model fields"):
        ValidationEngine(Customer, CleaningSettings(), checks)


def test_valid_customer_model_and_python_types(record_factory, customer_raw):
    item = record_factory(customer_raw)
    outcome = RowValidator(Customer).validate(item.data, item.data, item.lineage)
    assert outcome.rejects == []
    assert outcome.candidate is not None
    assert outcome.candidate.data["signup_date"].year == 2026


def test_multiple_independent_field_errors_are_collected(record_factory, customer_raw):
    bad = {**customer_raw, "customer_id": "bad", "email": "bad!", "credit_limit": "abc"}
    item = record_factory(bad)
    outcome = RowValidator(Customer).validate(bad, bad, item.lineage)
    assert outcome.candidate is None
    assert {issue.field for issue in outcome.rejects} == {"customer_id", "email", "credit_limit"}
    assert all(issue.raw_row_json and issue.batch_id == "batch-123" for issue in outcome.rejects)


def test_customer_row_level_end_date_rule(record_factory, customer_raw):
    bad = {**customer_raw, "end_date": "2025-01-01"}
    item = record_factory(bad)
    outcome = RowValidator(Customer).validate(bad, bad, item.lineage)
    assert len(outcome.rejects) == 1
    assert outcome.rejects[0].field == "__row__"
    assert "end_date" in outcome.rejects[0].error_message


def test_debit_row_level_positive_rule(record_factory):
    bad = dict(transaction_id="T001", customer_id="C001", transaction_date="2026-01-01",
               type="debit", amount="0", description=None)
    item = record_factory(bad)
    outcome = RowValidator(Transaction).validate(bad, bad, item.lineage)
    assert len(outcome.rejects) == 1
    assert "debit amount" in outcome.rejects[0].error_message


def test_dataset_uniqueness_and_foreign_keys(record_factory, customer_raw):
    rows = [
        record_factory(customer_raw, 2),
        record_factory({**customer_raw, "full_name": "Alice New"}, 3),
        record_factory({**customer_raw, "customer_id": "C099", "full_name": "Other Name"}, 4),
    ]
    checks = ChecksSettings(
        unique=["customer_id"],
        foreign_keys={"customer_id": ForeignKeySettings(values=["C001"])},
    )
    core = ValidationEngine(Customer, CleaningSettings(), checks).run(rows)
    assert core.rows_valid == 1
    assert core.rows_rejected == 2
    assert set(core.error_counts_by_type) == {"unique", "foreign_key"}
    assert core.rows_read == 3


def test_dataset_null_pct_and_row_count_threshold(record_factory, customer_raw):
    row = record_factory({**customer_raw, "email": None})
    core = ValidationEngine(
        Customer, CleaningSettings(), ChecksSettings(
            expected_row_count=RowCountSettings(min=2),
            max_null_pct={"email": 0.2},
        ),
    ).run([row])
    assert core.rows_valid == 1
    assert {issue.error_type for issue in core.dataset_issues} == {"row_count", "null_pct"}
    assert core.error_counts_by_field["email"] == 1


def test_empty_feed_and_upper_count_limit(record_factory, customer_raw):
    engine = ValidationEngine(Customer, CleaningSettings(), ChecksSettings(
        expected_row_count=RowCountSettings(min=1, max=1)))
    empty = engine.run([])
    assert empty.rows_valid == 0
    assert len(empty.dataset_issues) == 1
    two = engine.run([
        record_factory(customer_raw, 2),
        record_factory({**customer_raw, "customer_id": "C002"}, 3),
    ])
    assert len(two.dataset_issues) == 1
    assert two.dataset_issues[0].error_type == "row_count"


def test_duplicate_rows_drop_after_cleaning(record_factory, customer_raw):
    one = record_factory(customer_raw, 2)
    two = record_factory({**customer_raw, "full_name": " Alice Doe "}, 3)
    engine = ValidationEngine(Customer, CleaningSettings(), ChecksSettings(unique=["customer_id"]))
    core = engine.run([one, two])
    assert core.rows_read == 2
    assert core.rows_cleaned == 2
    assert core.duplicates_dropped == 1
    assert core.rows_rejected == 0
    assert core.rows_valid == 1


def test_column_collision_does_not_stop_feed(record_factory, customer_raw):
    first = record_factory({**customer_raw, "Customer ID": "C002"}, 2)
    second = record_factory(customer_raw, 3)
    core = ValidationEngine(Customer, CleaningSettings(), ChecksSettings()).run([first, second])
    assert core.rows_valid == 1
    assert core.rows_rejected == 1
    assert core.rejects[0].error_type == "cleaning"


def test_reference_csv_support(tmp_path, record_factory, customer_raw):
    ref = tmp_path / "refs.csv"
    ref.write_text("customer_id\nC001\nC002\n", encoding="utf-8")
    checker = DatasetChecker(ChecksSettings(foreign_keys={
        "customer_id": ForeignKeySettings(path="refs.csv", column="customer_id")
    }), tmp_path)
    assert checker.references["customer_id"] == {"C001", "C002"}
    core = ValidationEngine(Customer, CleaningSettings(), checker.settings, tmp_path).run([
        record_factory({**customer_raw, "customer_id": "C003"})
    ])
    assert core.error_counts_by_type["foreign_key"] == 1
