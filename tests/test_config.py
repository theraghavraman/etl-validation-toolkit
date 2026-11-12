from __future__ import annotations

import pytest
from pydantic import ValidationError

from etlguard.config import (
    ChecksSettings, ForeignKeySettings, RowCountSettings,
    SourceSettings, TargetSettings,
)
from etlguard.loaders.mysql import build_tables, sqlalchemy_type
from etlguard.examples.models import Customer


@pytest.mark.parametrize("bad", ["users;DROP TABLE x", "stg_test", "etl_run_audit", "bad-name",
                                 "etl_batch_commits", "ETL_BATCH_COMMITS", "STG_test"])
def test_unsafe_table_names_rejected(bad):
    with pytest.raises(ValidationError):
        TargetSettings(table=bad)


def test_upsert_and_scd2_require_keys():
    with pytest.raises(ValidationError):
        TargetSettings(table="users", mode="scd2")
    with pytest.raises(ValidationError):
        TargetSettings(table="users", mode="upsert")
    assert TargetSettings(table="users", mode="append").key == []


def test_invalid_config_ranges():
    with pytest.raises(ValidationError):
        ChecksSettings(max_null_pct={"email": 1.2})
    with pytest.raises(ValidationError):
        RowCountSettings(min=9, max=2)
    with pytest.raises(ValidationError):
        SourceSettings(type="csv")
    with pytest.raises(ValidationError):
        SourceSettings(type="api", url="https://example.com", max_retries=20)
    with pytest.raises(ValidationError):
        ForeignKeySettings(values=["1"], path="file.csv", column="id")


def test_build_tables_contain_target_and_staging_metadata():
    target = TargetSettings(table="customers", mode="scd2", key=["customer_id"])
    dim, stage = build_tables(Customer, target)
    assert dim.name == "customers"
    assert stage.name == "stg_customers"
    assert "effective_from" in dim.c
    assert "__etl_batch_id" in stage.c


def test_model_target_columns_exist():
    with pytest.raises(ValueError, match="must exist"):
        build_tables(Customer, TargetSettings(table="bad_model", mode="upsert", key=["absent"]))


def test_sqlalchemy_types_unsupported():
    with pytest.raises(ValueError, match="Unsupported"):
        sqlalchemy_type(list[str])


@pytest.mark.parametrize("delimiter", ["", "||", "\n", '"'])
def test_csv_requires_an_unambiguous_single_character_delimiter(delimiter):
    with pytest.raises(ValidationError, match="delimiter"):
        SourceSettings(type="csv", path="input.csv", delimiter=delimiter)
