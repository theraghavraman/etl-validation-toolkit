"""Fast SQLAlchemy transactional smoke tests (CI also runs against real MySQL 8)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, Optional
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import MetaData, Table, create_engine, select

from etlguard.audit import AuditWriter
from etlguard.config import TargetSettings
from etlguard.examples.models import Customer
from etlguard.loaders.mysql import MysqlLoader, build_tables, sqlalchemy_type
from etlguard.pipeline import RunResult
from etlguard.reconcile import LoadCounts, ReconciliationError
from etlguard.sources.base import Lineage
from etlguard.validation import Candidate


def candidate(pk="C001", name="Alice Doe", limit="100.00"):
    model = Customer.model_validate({
        "customer_id": pk, "full_name": name,
        "signup_date": "2026-01-01", "status": "active", "credit_limit": limit,
    })
    return Candidate(data=model.model_dump(mode="python"), cleaned={}, raw={},
                     lineage=Lineage("synthetic", 2, datetime.now(timezone.utc), "unit-batch"))


def rows(engine, name):
    table = Table(name, MetaData(), autoload_with=engine)
    with engine.connect() as conn:
        return conn.execute(select(table)).mappings().all()


@pytest.fixture
def sqlite_loader():
    engine = create_engine("sqlite:///:memory:")
    try:
        yield MysqlLoader(engine=engine)
    finally:
        engine.dispose()


def test_mysql_loader_append_stage_and_replace_smoke(sqlite_loader):
    target = TargetSettings(table="unit_customers", mode="append", batch_size=1)
    first = sqlite_loader.load([candidate(), candidate("C002")], str(uuid4()), Customer, target)
    assert first == LoadCounts(2, 0)
    assert len(rows(sqlite_loader.engine, "unit_customers")) == 2
    assert rows(sqlite_loader.engine, "stg_unit_customers") == []
    replacement = TargetSettings(table="unit_customers", mode="truncate_insert")
    count = sqlite_loader.load([candidate("C003")], str(uuid4()), Customer, replacement)
    assert count.loaded == 1
    assert [row["customer_id"] for row in rows(sqlite_loader.engine, "unit_customers")] == ["C003"]


def test_scd2_versions_and_unchanged_smoke(sqlite_loader):
    target = TargetSettings(table="unit_scd2", mode="scd2", key=["customer_id"],
                            tracked_columns=["full_name"])
    first = sqlite_loader.load([candidate()], str(uuid4()), Customer, target)
    second = sqlite_loader.load([candidate(name="Alice Updated")], str(uuid4()), Customer, target)
    third = sqlite_loader.load([candidate(name="Alice Updated")], str(uuid4()), Customer, target)
    assert (first.inserted, second.versions_created, third.unchanged) == (1, 1, 1)
    history = rows(sqlite_loader.engine, "unit_scd2")
    assert len(history) == 2
    assert sum(record["is_current"] for record in history) == 1
    old = next(record for record in history if not record["is_current"])
    current = next(record for record in history if record["is_current"])
    assert old["effective_to"] == current["effective_from"]


def test_reconciliation_rollback_smoke(sqlite_loader, monkeypatch):
    target = TargetSettings(table="unit_rollback", mode="append")
    original = sqlite_loader._merge

    def mismatch(conn, table, data, settings):
        original(conn, table, data, settings)
        return LoadCounts(0, 0)

    monkeypatch.setattr(sqlite_loader, "_merge", mismatch)
    with pytest.raises(ReconciliationError, match="mismatch"):
        sqlite_loader.load([candidate()], str(uuid4()), Customer, target)
    assert rows(sqlite_loader.engine, "unit_rollback") == []
    assert rows(sqlite_loader.engine, "stg_unit_rollback") == []


def test_missing_business_unique_constraint_fails_without_writing(sqlite_loader):
    sqlite_loader.load([candidate()], str(uuid4()), Customer,
                       TargetSettings(table="unit_no_unique", mode="append"))
    with pytest.raises(ValueError, match="no UNIQUE key"):
        sqlite_loader.load([candidate()], str(uuid4()), Customer,
                           TargetSettings(table="unit_no_unique", mode="upsert",
                                          key=["customer_id"]))
    assert len(rows(sqlite_loader.engine, "unit_no_unique")) == 1


def test_model_type_mapping_and_safe_table_validation():
    for annotation in (str, int, float, bool, Decimal, date, datetime, Optional[int], Literal["a", "b"]):
        assert sqlalchemy_type(annotation) is not None
    with pytest.raises(ValueError, match="Unsupported"):
        sqlalchemy_type(dict)

    target, stage = build_tables(Customer, TargetSettings(table="unit_model", mode="upsert",
                                                       key=["customer_id"]))
    assert "customer_id" in stage.c
    assert "__etl_batch_id" in stage.c
    assert any(set(item.columns.keys()) == {"customer_id"} for item in target.constraints
               if item.__class__.__name__ == "UniqueConstraint")
    assert target.c["full_name"].type.length == 80

    with pytest.raises(ValueError, match="target.key"):
        build_tables(Customer, TargetSettings(table="unit_model", mode="upsert", key=["bogus"]))

    class ReservedName(BaseModel):
        effective_from: str

    with pytest.raises(ValueError, match="reserved"):
        build_tables(ReservedName, TargetSettings(table="unit_model", mode="append"))

    with pytest.raises(ValueError, match="mysql\\+pymysql"):
        MysqlLoader(database_url="sqlite:///:memory:")


def test_audit_writer_smoke(sqlite_loader):
    engine = sqlite_loader.engine
    now = datetime.now(timezone.utc)
    result = RunResult(batch_id=str(uuid4()), pipeline="unit", source="synthetic",
                       started_at=now, finished_at=now, status="success",
                       rows_read=2, rows_valid=2, rows_rejected=0, rows_loaded=2)
    AuditWriter(engine).write(result, "synthetic")
    assert rows(engine, "etl_run_audit")[0]["status"] == "success"


def test_empty_truncate_insert_clears_target(sqlite_loader):
    table = "unit_empty_replace"
    initial = TargetSettings(table=table, mode="append")
    sqlite_loader.load([candidate()], str(uuid4()), Customer, initial)
    with pytest.raises(ValueError, match="no valid records"):
        sqlite_loader.load([], str(uuid4()), Customer,
                           TargetSettings(table=table, mode="truncate_insert"))
    assert len(rows(sqlite_loader.engine, table)) == 1
    replaced = sqlite_loader.load([], str(uuid4()), Customer,
                                  TargetSettings(table=table, mode="truncate_insert"),
                                  allow_empty_replace=True)
    assert replaced.loaded == 0
    assert rows(sqlite_loader.engine, table) == []
