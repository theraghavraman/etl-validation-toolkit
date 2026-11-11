"""Regression tests for source-row accuracy and destructive load safety."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import MetaData, Table, UniqueConstraint, create_engine, select

from etlguard.config import ChecksSettings, CleaningSettings, TargetSettings
from etlguard.core import ValidationEngine
from etlguard.examples.models import Customer
from etlguard.loaders.mysql import MysqlLoader, build_tables
from etlguard.pipeline import Pipeline
from etlguard.sources.base import SourceRecord, lineage
from etlguard.validation import Candidate


def config_file(tmp_path: Path, csv_data: str, policy=None, mode="truncate_insert", name="safe_job"):
    (tmp_path / "input.csv").write_text(csv_data, encoding="utf-8")
    cfg = {
        "pipeline": name,
        "source": {"type": "csv", "path": "input.csv"},
        "model": "etlguard.examples.models:Customer",
        "cleaning": {"date_columns": ["signup_date"], "number_columns": ["credit_limit"]},
        "target": {"table": "target_customers", "mode": mode},
        "policy": policy or {},
    }
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


HEADER = "customer_id,full_name,email,signup_date,status,credit_limit\n"
BAD_ROW = "C001,X,bad!,invalid-date,active,-20\n"
GOOD_ROW = "C001,Alice Doe,alice@example.com,2026-01-01,active,120.00\n"


def test_identical_invalid_records_are_all_rejected(tmp_path):
    path = config_file(tmp_path, HEADER + BAD_ROW * 100,
                       policy={"fail_if_reject_rate_above": 0.05}, mode="append")
    outcome = Pipeline.from_yaml(path).run(dry_run=True)
    assert outcome.rows_read == 100
    assert outcome.rows_rejected == 100
    assert outcome.duplicates_dropped == 0
    assert outcome.rows_valid == 0
    assert outcome.status == "failed"
    assert any("reject rate 100.00%" in issue for issue in outcome.failures)


def test_only_identical_valid_rows_are_dropped():
    first = dict(customer_id="C001", full_name="Alice Doe", email="alice@example.com",
                 signup_date="2026-01-01", status="active", credit_limit="120.00")
    rows = [SourceRecord(first, lineage("synthetic", i, "batch")) for i in range(2, 5)]
    result = ValidationEngine(Customer, CleaningSettings(), ChecksSettings()).run(rows)
    assert (result.rows_valid, result.rows_rejected, result.duplicates_dropped) == (1, 0, 2)


def test_empty_target_replacement_is_blocked_by_default(tmp_path):
    path = config_file(tmp_path, HEADER)
    outcome = Pipeline.from_yaml(path).run(dry_run=True)
    assert outcome.status == "failed"
    assert any("source is empty" in issue for issue in outcome.failures)


def test_empty_target_replacement_requires_explicit_opt_in(tmp_path):
    path = config_file(tmp_path, HEADER, policy={"allow_empty_replace": True})
    assert Pipeline.from_yaml(path).run(dry_run=True).status == "dry_run"


def test_partial_target_replacement_blocked_unless_explicitly_allowed(tmp_path):
    path = config_file(tmp_path, HEADER + GOOD_ROW + BAD_ROW)
    outcome = Pipeline.from_yaml(path).run(dry_run=True)
    assert outcome.status == "failed"
    assert outcome.rows_valid == 1 and outcome.rows_rejected == 1
    assert any("rejected source records" in issue for issue in outcome.failures)
    path = config_file(tmp_path, HEADER + GOOD_ROW + BAD_ROW,
                       policy={"allow_partial_replace": True})
    assert Pipeline.from_yaml(path).run(dry_run=True).status == "dry_run"


def test_rejects_path_traversal_in_pipeline_identifier(tmp_path):
    path = config_file(tmp_path, HEADER, name="../escape")
    with pytest.raises(ValueError, match="pipeline"):
        Pipeline.from_yaml(path)


def test_upsert_rejects_competing_unique_constraint_before_load():
    engine = create_engine("sqlite:///:memory:")
    try:
        target = TargetSettings(table="business_customers", mode="upsert", key=["customer_id"])
        target_def, stage_def = build_tables(Customer, target)
        target_def.append_constraint(UniqueConstraint("email", name="uq_email_also"))
        target_def.metadata.create_all(engine)
        model = Customer.model_validate({
            "customer_id": "C001", "full_name": "Alice Doe", "signup_date": "2026-01-01",
            "status": "active", "email": "alice@example.com", "credit_limit": 100,
        })
        candidate = Candidate(model.model_dump(mode="python"), {}, {},
                              lineage("synthetic", 2, str(uuid4())))
        with pytest.raises(ValueError, match="competing UNIQUE keys"):
            MysqlLoader(engine=engine).load([candidate], str(uuid4()), Customer, target)
        current = Table(target.table, MetaData(), autoload_with=engine)
        with engine.connect() as conn:
            assert conn.execute(select(current)).all() == []
    finally:
        engine.dispose()


def test_identical_invalid_foreign_keys_are_not_deduplicated():
    from etlguard.config import ForeignKeySettings

    raw = dict(customer_id="C999", full_name="Alice Doe", signup_date="2026-01-01",
               status="active", credit_limit="100", email="alice@example.com")
    records = [SourceRecord(raw, lineage("synthetic", i, "batch")) for i in range(2, 5)]
    checks = ChecksSettings(foreign_keys={"customer_id": ForeignKeySettings(values=["C001"])})
    result = ValidationEngine(Customer, CleaningSettings(), checks).run(records)
    assert (result.rows_valid, result.rows_rejected, result.duplicates_dropped) == (0, 3, 0)
    assert result.error_counts_by_type["foreign_key"] == 3


def test_repeated_business_key_violations_are_all_rejected():
    base = dict(customer_id="C001", full_name="Alice Doe", signup_date="2026-01-01",
                status="active", credit_limit="100", email="alice@example.com")
    differing = {**base, "full_name": "Bob Smith"}
    records = [SourceRecord(row, lineage("synthetic", i, "batch"))
               for i, row in enumerate([base, differing, differing], start=2)]
    result = ValidationEngine(Customer, CleaningSettings(),
                              ChecksSettings(unique=["customer_id"])).run(records)
    assert (result.rows_valid, result.rows_rejected, result.duplicates_dropped) == (1, 2, 0)
    assert result.error_counts_by_type["unique"] == 2


def test_rename_map_without_column_normalization():
    from etlguard.cleaning import Cleaner

    cleaner = Cleaner(CleaningSettings(
        snake_case_columns=False, rename_map={"Input ID": "customer_id"}
    ))
    assert cleaner.clean({"Input ID": " C001 "}) == {"customer_id": "C001"}
