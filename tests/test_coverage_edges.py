"""Edge cases for API contract, model typing and dataset checks."""

from __future__ import annotations

from decimal import Decimal


from etlguard.cleaning import Cleaner
from etlguard.config import ChecksSettings, CleaningSettings, ForeignKeySettings
from etlguard.core import ValidationEngine
from etlguard.examples.models import Customer
from etlguard.loaders.mysql import batches, sqlalchemy_type
from etlguard.validation import RowValidator


def test_batching_splits_work():
    assert batches([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert batches([], 2) == []


def test_decimal_types():
    assert sqlalchemy_type(Decimal).scale == 2
    assert sqlalchemy_type(str | None).length == 255
    assert "DATETIME" in str(sqlalchemy_type(__import__("datetime").datetime)).upper()


def test_dataset_reference_values_are_strings():
    checks = ChecksSettings(foreign_keys={"customer_id": ForeignKeySettings(values=[1, 2])})
    from etlguard.checks import DatasetChecker

    checker = DatasetChecker(checks, __import__("pathlib").Path("."))
    assert checker.references["customer_id"] == {"1", "2"}


def test_core_no_duplicate_drop_can_fail_uniqueness(record_factory, customer_raw):
    first = record_factory(customer_raw, 2)
    second = record_factory(customer_raw, 3)
    engine = ValidationEngine(Customer, CleaningSettings(drop_duplicates=False),
                              ChecksSettings(unique=["customer_id"]))
    result = engine.run([first, second])
    assert result.rows_valid == 1
    assert result.rows_rejected == 1
    assert result.duplicates_dropped == 0


def test_raw_unsupported_type_preserved_as_error(record_factory, customer_raw):
    raw = {**customer_raw, "credit_limit": {"unexpected": "object"}}
    rec = record_factory(raw)
    outcome = RowValidator(Customer).validate(raw, raw, rec.lineage)
    assert outcome.candidate is None
    assert outcome.rejects[0].field == "credit_limit"


def test_cleaner_custom_null_token():
    cleaner = Cleaner(CleaningSettings(null_tokens=["MISSING"]))
    assert cleaner.clean({"name": "Missing"})["name"] is None
    assert cleaner.clean({"name": "NA"})["name"] == "NA"
