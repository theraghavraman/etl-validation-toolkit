"""Regression checks for atomic auditing, retry safety and SCD2 uniqueness."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import (Boolean, Column, DateTime, MetaData, String, Table,
                        create_engine, select)
from sqlalchemy.exc import IntegrityError

from etlguard.audit import AuditWriter, BatchConflictError
from etlguard.config import TargetSettings
from etlguard.examples.models import Customer
from etlguard.loaders.mysql import MysqlLoader, _model_columns
from etlguard.pipeline import RunResult
from etlguard.sources.base import Lineage
from etlguard.validation import Candidate


def item(pk="C001", name="Alice"):
    record = Customer.model_validate({
        "customer_id": pk, "full_name": name, "email": f"{pk}@example.com",
        "signup_date": "2026-01-01", "status": "active", "credit_limit": 100,
    })
    return Candidate(data=record.model_dump(mode="python"), cleaned={}, raw={},
                     lineage=Lineage("csv", 2, datetime.now(timezone.utc), "source"))


@pytest.fixture
def loader():
    engine = create_engine("sqlite:///:memory:")
    try:
        yield MysqlLoader(engine=engine)
    finally:
        engine.dispose()


def table_rows(loader, name):
    table = Table(name, MetaData(), autoload_with=loader.engine)
    with loader.engine.connect() as conn:
        return conn.execute(select(table)).mappings().all()


def test_direct_append_is_idempotent_and_audited(loader):
    target = TargetSettings(table="safe_append", mode="append")
    batch_id = str(uuid4())
    first = loader.load([item()], batch_id, Customer, target)
    second = loader.load([item()], batch_id, Customer, target)
    assert first.loaded == second.loaded == 1
    assert not first.replayed and second.replayed
    assert len(table_rows(loader, target.table)) == 1
    assert len(table_rows(loader, "etl_batch_commits")) == 1
    assert table_rows(loader, "etl_run_audit")[0]["status"] == "success"


def test_batch_conflict_rejects_changed_input(loader):
    target = TargetSettings(table="safe_conflict", mode="append")
    batch_id = str(uuid4())
    loader.load([item()], batch_id, Customer, target)
    with pytest.raises(BatchConflictError, match="different input"):
        loader.load([item(name="Mallory")], batch_id, Customer, target)
    assert table_rows(loader, target.table)[0]["full_name"] == "Alice"


def test_audit_exception_rolls_back_target_and_ledger(loader, monkeypatch):
    batch = str(uuid4())
    target = TargetSettings(table="safe_audit_failure", mode="append")
    original = AuditWriter.record_success

    def outage(self, conn, result, source, counts):
        original(self, conn, result, source, counts)
        raise RuntimeError("simulated audit failure before commit")

    with monkeypatch.context() as patch:
        patch.setattr(AuditWriter, "record_success", outage)
        with pytest.raises(RuntimeError, match="audit failure"):
            loader.load([item()], batch, Customer, target)
    assert table_rows(loader, target.table) == []
    assert table_rows(loader, "etl_batch_commits") == []
    assert table_rows(loader, "etl_run_audit") == []
    assert loader.load([item()], batch, Customer, target).loaded == 1


def test_lost_commit_response_is_recovered_from_durable_record(loader, monkeypatch):
    target = TargetSettings(table="safe_lost_reply", mode="append")
    original = loader._locked_connection

    @contextmanager
    def lose_reply(config):
        with original(config) as conn:
            yield conn
        raise ConnectionError("response lost after commit")

    monkeypatch.setattr(loader, "_locked_connection", lose_reply)
    result = loader.load([item()], str(uuid4()), Customer, target)
    assert result.loaded == 1 and result.replayed
    assert len(table_rows(loader, target.table)) == 1
    assert table_rows(loader, "etl_run_audit")[0]["status"] == "success"


def test_failure_audit_then_success_same_id(loader):
    batch_id = str(uuid4())
    now = datetime.now(timezone.utc)
    run = RunResult(batch_id=batch_id, pipeline="direct_api", source="direct_api",
                    started_at=now, finished_at=now, status="failed")
    auditor = AuditWriter(loader.engine)
    auditor.record_failure(run, run.source)
    assert table_rows(loader, "etl_run_audit")[0]["status"] == "failed"
    loader.load([item()], batch_id, Customer, TargetSettings(table="recovered", mode="append"))
    assert table_rows(loader, "etl_run_audit")[0]["status"] == "success"
    run.status = "failed"
    auditor.record_failure(run, run.source)
    assert table_rows(loader, "etl_run_audit")[0]["status"] == "success"


def test_safe_replace_minimum_size_enforced(loader):
    batch_id = str(uuid4())
    loader.load([item("C001"), item("C002")], batch_id, Customer,
                TargetSettings(table="safe_replace", mode="append"))
    target = TargetSettings(table="safe_replace", mode="truncate_insert",
                            min_replace_ratio=0.75)
    with pytest.raises(ValueError, match="min_replace_ratio"):
        loader.load([item("C003")], str(uuid4()), Customer, target)
    assert len(table_rows(loader, "safe_replace")) == 2
    assert len(table_rows(loader, "etl_batch_commits")) == 1


def test_generated_scd2_active_unique_constraint(loader):
    target = TargetSettings(table="safe_scd2", mode="scd2", key=["customer_id"],
                            tracked_columns=["full_name"])
    loader.load([item()], str(uuid4()), Customer, target)
    table = Table(target.table, MetaData(), autoload_with=loader.engine)
    assert "__etl_current_marker" in table.c
    assert table.c["__etl_current_marker"].computed is not None
    with pytest.raises(IntegrityError):
        with loader.engine.begin() as conn:
            conn.execute(table.insert().values(
                __etl_id=str(uuid4()), **item(name="Bob").data,
                effective_from=datetime.now(timezone.utc).replace(tzinfo=None),
                effective_to=None, is_current=True,
            ))
    assert len(table_rows(loader, target.table)) == 1


def test_legacy_scd2_without_db_guard_fails_closed(loader):
    target = TargetSettings(table="safe_old_scd2", mode="scd2", key=["customer_id"])
    meta = MetaData()
    Table(target.table, meta,
          Column("__etl_id", String(36), primary_key=True), *_model_columns(Customer),
          Column("effective_from", DateTime(), nullable=False),
          Column("effective_to", DateTime()), Column("is_current", Boolean(), nullable=False))
    meta.create_all(loader.engine)
    with pytest.raises(ValueError, match="UNIQUE active-row guard"):
        loader.load([item()], str(uuid4()), Customer, target)
    assert table_rows(loader, target.table) == []


def test_invalid_batch_id_does_not_write(loader):
    with pytest.raises(ValueError, match="canonical UUID"):
        loader.load([item()], "some id", Customer, TargetSettings(table="unsafe", mode="append"))


def test_partial_source_failure_preserves_counts_without_loading():
    from etlguard.config import ChecksSettings, CleaningSettings
    from etlguard.core import ValidationEngine
    from etlguard.sources.base import SourceRecord

    def partial_source():
        yield SourceRecord(data=item().data, lineage=Lineage(
            "csv", 2, datetime.now(timezone.utc), "partial"
        ))
        raise OSError("midstream feed disconnect")

    outcome = ValidationEngine(Customer, CleaningSettings(), ChecksSettings()).run(partial_source())
    assert (outcome.rows_read, outcome.rows_valid, outcome.rows_rejected) == (1, 1, 0)
    assert "midstream feed disconnect" in outcome.source_error


def test_full_pipeline_replays_identical_batch_and_preserves_success_audit(
    tmp_path, monkeypatch,
):
    import yaml
    from etlguard.pipeline import Pipeline

    db_engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite'}")
    from etlguard.loaders.mysql import MysqlLoader as RealLoader

    def sqlite_init(self, database_url=None, engine=None):
        self.engine = db_engine

    monkeypatch.setattr(RealLoader, "__init__", sqlite_init)
    monkeypatch.setenv("ETLGUARD_DATABASE_URL", "mysql+pymysql://not-used")
    file = tmp_path / "feed.csv"
    file.write_text(
        "customer_id,full_name,email,signup_date,status,credit_limit\n"
        "C001,Alice,C001@example.com,2026-01-01,active,100\n", encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "pipeline": "once_only", "source": {"type": "csv", "path": "feed.csv"},
        "model": "etlguard.examples.models:Customer",
        "target": {"table": "once_only", "mode": "append"},
    }), encoding="utf-8")
    batch = str(uuid4())
    first = Pipeline.from_yaml(config).run(batch_id=batch)
    second = Pipeline.from_yaml(config).run(batch_id=batch)
    assert (first.status, second.status) == ("success", "success")
    assert not first.replayed and second.replayed
    assert first.durable_commit and second.durable_commit
    assert first.request_hash == second.request_hash
    assert len(table_rows(RealLoader(engine=db_engine), "once_only")) == 1
    file.write_text(
        "customer_id,full_name,email,signup_date,status,credit_limit\n"
        "C002,Bob,C002@example.com,2026-01-01,active,100\n", encoding="utf-8",
    )
    changed = Pipeline.from_yaml(config).run(batch_id=batch)
    assert changed.status == "failed"
    assert any("different input" in message for message in changed.failures)
    assert table_rows(RealLoader(engine=db_engine), "etl_run_audit")[0]["status"] == "success"
    assert len(table_rows(RealLoader(engine=db_engine), "once_only")) == 1
    db_engine.dispose()


def test_pipeline_run_tracks_partial_source_and_refuses_to_load(monkeypatch, tmp_path):
    import yaml
    from etlguard.pipeline import Pipeline
    from etlguard.sources.base import SourceRecord
    from etlguard.sources.csv import CsvSource

    def broken_source(self, batch_id):
        yield SourceRecord(item().data, Lineage("csv", 2, datetime.now(timezone.utc), batch_id))
        raise OSError("connection failed during source read")

    monkeypatch.setattr(CsvSource, "read", broken_source)
    configuration = tmp_path / "pipe.yaml"
    configuration.write_text(yaml.safe_dump({
        "pipeline": "partial_job", "source": {"type": "csv", "path": "unused.csv"},
        "model": "etlguard.examples.models:Customer",
    }), encoding="utf-8")
    result = Pipeline.from_yaml(configuration).run(dry_run=True)
    assert result.status == "failed"
    assert result.rows_read == 1 and result.rows_valid == 1
    assert any("connection failed during source read" in m for m in result.failures)


def test_cli_exposes_stable_batch_retry_and_status_commands(tmp_path):
    from typer.testing import CliRunner
    from etlguard.cli import app

    from rich.text import Text

    cli = CliRunner()
    assert "--batch-id" in Text.from_ansi(cli.invoke(app, ["run", "--help"]).output).plain
    assert "batch-status" in Text.from_ansi(cli.invoke(app, ["--help"]).output).plain
    assert cli.invoke(app, ["batch-status", "not-a-uuid"]).exit_code == 2
    assert cli.invoke(app, ["upgrade-scd2", str(tmp_path / "missing.yaml")]).exit_code == 2


def test_cli_db_status_and_upgrade_guard_with_sqlite(monkeypatch, tmp_path):
    import yaml
    from typer.testing import CliRunner
    from etlguard.cli import app
    from etlguard.loaders.mysql import MysqlLoader as RealLoader

    engine = create_engine(f"sqlite:///{tmp_path / 'cli.sqlite'}")
    batch_id = str(uuid4())
    target = TargetSettings(table="cli_test", mode="append")
    MysqlLoader(engine=engine).load([item()], batch_id, Customer, target)

    def inject_engine(self, database_url=None, engine=None):
        self.engine = engine or engine_obj

    engine_obj = engine
    monkeypatch.setattr(RealLoader, "__init__", inject_engine)
    monkeypatch.setenv("ETLGUARD_DATABASE_URL", "mysql+pymysql://stub")
    runner = CliRunner()
    found = runner.invoke(app, ["batch-status", batch_id])
    assert found.exit_code == 0, found.output
    assert "rows_inserted" in found.output
    missing = runner.invoke(app, ["batch-status", str(uuid4())])
    assert missing.exit_code == 1
    setup = runner.invoke(app, ["init-db"])
    assert setup.exit_code == 0 and "etl_batch_commits" in setup.output
    config = tmp_path / "scd2.yaml"
    config.write_text(yaml.safe_dump({
        "pipeline": "legacy", "source": {"type": "csv", "path": "unused.csv"},
        "model": "etlguard.examples.models:Customer",
        "target": {"table": "cli_test", "mode": "scd2", "key": ["customer_id"]},
    }), encoding="utf-8")
    unsupported = runner.invoke(app, ["upgrade-scd2", str(config), "--yes"])
    assert unsupported.exit_code == 2
    assert "requires MySQL 8" in unsupported.output
    engine.dispose()


def test_post_commit_summary_io_problem_is_warning_not_failed_load(tmp_path, monkeypatch):
    import yaml
    from etlguard.pipeline import Pipeline
    from etlguard.loaders.mysql import MysqlLoader as RealLoader

    db_engine = create_engine(f"sqlite:///{tmp_path / 'io_test.sqlite'}")

    def inject_engine(self, database_url=None, engine=None):
        self.engine = db_engine

    monkeypatch.setattr(RealLoader, "__init__", inject_engine)
    monkeypatch.setenv("ETLGUARD_DATABASE_URL", "mysql+pymysql://stub")
    (tmp_path / "input.csv").write_text(
        "customer_id,full_name,email,signup_date,status,credit_limit\n"
        "C001,Alice,C001@example.com,2026-01-01,active,100\n", encoding="utf-8",
    )
    config = tmp_path / "pipeline.yaml"
    config.write_text(yaml.safe_dump({
        "pipeline": "report_io", "source": {"type": "csv", "path": "input.csv"},
        "model": "etlguard.examples.models:Customer",
        "target": {"table": "report_io", "mode": "append"},
    }), encoding="utf-8")
    from pathlib import Path
    original_write = Path.write_text

    def reject_summary_write(self, *args, **kwargs):
        if self.name.endswith("_summary.json"):
            raise OSError("report disk full")
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", reject_summary_write)
    result = Pipeline.from_yaml(config).run(batch_id=str(uuid4()))
    assert result.status == "success" and result.durable_commit
    assert result.summary_json is None
    assert any("report disk full" in issue for issue in result.warnings)
    assert len(table_rows(RealLoader(engine=db_engine), "report_io")) == 1
    db_engine.dispose()


def test_mysql_key_with_nullable_column_is_rejected_even_with_unique_index(loader):
    target = TargetSettings(table="unsafe_null_key", mode="upsert", key=["email"])
    with pytest.raises(ValueError, match="NOT NULL"):
        loader.load([item()], str(uuid4()), Customer, target)
    assert table_rows(loader, target.table) == []


def test_target_rejects_repeated_business_key_fields():
    with pytest.raises(ValueError, match="duplicate"):
        TargetSettings(table="unsafe_duplicate_key", mode="upsert",
                       key=["customer_id", "customer_id"])


def test_direct_loader_rejects_duplicate_business_keys_before_writing(loader):
    target = TargetSettings(table="duplicate_upsert", mode="upsert", key=["customer_id"])
    with pytest.raises(ValueError, match="Duplicate business keys"):
        loader.load([item(), item(name="Bob")], str(uuid4()), Customer, target)
