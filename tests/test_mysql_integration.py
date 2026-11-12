"""Real MySQL 8 integration tests; CI provisions a service container."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, Table, create_engine, select

from etlguard.audit import AuditWriter
from etlguard.config import TargetSettings
from etlguard.examples.models import Customer
from etlguard.loaders.mysql import MysqlLoader
from etlguard.pipeline import Pipeline, RunResult
from etlguard.reconcile import LoadCounts, ReconciliationError
from etlguard.sources.base import Lineage
from etlguard.validation import Candidate

pytestmark = pytest.mark.integration


@pytest.fixture
def mysql_engine():
    url = os.getenv("ETLGUARD_TEST_MYSQL_URL")
    if not url:
        pytest.skip("Set ETLGUARD_TEST_MYSQL_URL to run MySQL 8 integration tests")
    engine = create_engine(url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def table_name(mysql_engine):
    name = "it_" + uuid4().hex[:16]
    yield name
    metadata = MetaData()
    with mysql_engine.begin() as conn:
        for table_name in [f"stg_{name}", name]:
            table = Table(table_name, metadata, autoload_with=conn)
            table.drop(conn, checkfirst=True)


def candidate(pk="C001", name="Alice Doe", limit="100.00", row_number=2):
    item = Customer.model_validate({
        "customer_id": pk, "full_name": name, "email": f"{pk.lower()}@example.com",
        "signup_date": "2026-01-01", "status": "active", "credit_limit": limit,
    })
    return Candidate(
        data=item.model_dump(mode="python"), cleaned=item.model_dump(mode="python"), raw={},
        lineage=Lineage("integration", row_number, datetime.now(timezone.utc), "test"),
    )


def target_rows(engine, table_name):
    table = Table(table_name, MetaData(), autoload_with=engine)
    with engine.connect() as conn:
        return conn.execute(select(table)).mappings().all()


def test_upsert_insert_and_update(mysql_engine, table_name):
    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="upsert", key=["customer_id"], batch_size=1)
    first = loader.load([candidate("C001"), candidate("C002")], str(uuid4()), Customer, target)
    assert first == LoadCounts(2, 0)
    second = loader.load([candidate("C001", "Updated Name"), candidate("C003")],
                         str(uuid4()), Customer, target)
    assert second == LoadCounts(1, 1)
    rows = target_rows(mysql_engine, table_name)
    assert len(rows) == 3
    assert next(row for row in rows if row["customer_id"] == "C001")["full_name"] == "Updated Name"


def test_scd2_tracks_history_and_unchanged(mysql_engine, table_name):
    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="scd2", key=["customer_id"],
                            tracked_columns=["full_name"], batch_size=1)
    assert loader.load([candidate()], str(uuid4()), Customer, target).inserted == 1
    update = loader.load([candidate(name="Alice Updated")], str(uuid4()), Customer, target)
    assert (update.updated, update.versions_created) == (1, 1)
    unchanged = loader.load([candidate(name="Alice Updated")], str(uuid4()), Customer, target)
    assert (unchanged.updated, unchanged.unchanged) == (1, 1)
    rows = target_rows(mysql_engine, table_name)
    assert len(rows) == 2
    assert len([row for row in rows if row["is_current"]]) == 1
    old = next(row for row in rows if not row["is_current"])
    assert old["effective_to"] is not None
    current = next(row for row in rows if row["is_current"])
    assert current["effective_to"] is None
    assert old["effective_to"] <= current["effective_from"]


def test_truncate_insert_is_transactional_delete(mysql_engine, table_name):
    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="truncate_insert")
    loader.load([candidate("C001"), candidate("C002")], str(uuid4()), Customer, target)
    assert len(target_rows(mysql_engine, table_name)) == 2
    loader.load([candidate("C003")], str(uuid4()), Customer, target)
    assert [row["customer_id"] for row in target_rows(mysql_engine, table_name)] == ["C003"]


def test_append_inserts_and_reconciles(mysql_engine, table_name):
    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="append")
    assert loader.load([candidate()], str(uuid4()), Customer, target).loaded == 1
    assert loader.load([candidate("C002")], str(uuid4()), Customer, target).loaded == 1
    assert len(target_rows(mysql_engine, table_name)) == 2


def test_reconciliation_mismatch_rolls_back_stage_and_target(mysql_engine, table_name, monkeypatch):
    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="append")
    original = loader._merge

    def faulty_merge(conn, table, records, config):
        original(conn, table, records, config)
        return LoadCounts(0, 0)  # Deliberate mismatch after target writes.

    monkeypatch.setattr(loader, "_merge", faulty_merge)
    with pytest.raises(ReconciliationError):
        loader.load([candidate()], str(uuid4()), Customer, target)
    assert target_rows(mysql_engine, table_name) == []
    assert target_rows(mysql_engine, f"stg_{table_name}") == []


def test_transaction_rollback_on_merge_failure(mysql_engine, table_name, monkeypatch):
    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="append")
    original = loader._merge

    def broken_merge(conn, table, records, config):
        original(conn, table, records, config)
        raise RuntimeError("injected crash after insert")

    monkeypatch.setattr(loader, "_merge", broken_merge)
    with pytest.raises(RuntimeError, match="injected"):
        loader.load([candidate()], str(uuid4()), Customer, target)
    assert target_rows(mysql_engine, table_name) == []
    assert target_rows(mysql_engine, f"stg_{table_name}") == []


def test_audit_table_rows_persist(mysql_engine):
    audit = AuditWriter(mysql_engine)
    audit.init_db()
    batch_id = str(uuid4())
    now = datetime.now(timezone.utc)
    result = RunResult(
        batch_id=batch_id, pipeline="integration", source="synthetic.csv",
        started_at=now, finished_at=now, status="success",
        rows_read=2, rows_valid=2, rows_rejected=0, rows_loaded=2,
    )
    audit.write(result, "synthetic.csv")
    with mysql_engine.connect() as conn:
        row = conn.execute(select(audit.table).where(audit.table.c.batch_id == batch_id)).first()
    assert row is not None
    assert row.status == "success"
    assert row.rows_loaded == 2


def test_pipeline_creates_success_audit(mysql_engine, table_name, monkeypatch, tmp_path):
    import yaml

    url = os.environ["ETLGUARD_TEST_MYSQL_URL"]
    monkeypatch.setenv("ETLGUARD_DATABASE_URL", url)
    (tmp_path / "input.csv").write_text(
        "customer_id,full_name,email,signup_date,status,credit_limit\n"
        "C001,Alice Doe,alice@example.com,2026-01-01,active,100\n",
        encoding="utf-8",
    )
    config = {
        "pipeline": "integration_job",
        "source": {"type": "csv", "path": "input.csv"},
        "model": "etlguard.examples.models:Customer",
        "cleaning": {"date_columns": ["signup_date"], "number_columns": ["credit_limit"]},
        "target": {"table": table_name, "mode": "upsert", "key": ["customer_id"]},
    }
    file = tmp_path / "pipeline.yaml"
    file.write_text(yaml.safe_dump(config))
    result = Pipeline.from_yaml(file).run()
    assert result.status == "success", result.failures
    assert result.rows_loaded == 1
    audit = AuditWriter(mysql_engine)
    with mysql_engine.connect() as conn:
        entry = conn.execute(select(audit.table).where(audit.table.c.batch_id == result.batch_id)).first()
    assert entry.status == "success"


def test_atomic_batch_commit_and_append_retry_on_mysql8(mysql_engine, table_name):
    from etlguard.audit import BatchConflictError

    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="append")
    batch_id = str(uuid4())
    first = loader.load([candidate()], batch_id, Customer, target)
    replay = loader.load([candidate()], batch_id, Customer, target)
    assert first.loaded == replay.loaded == 1
    assert replay.replayed
    assert len(target_rows(mysql_engine, table_name)) == 1
    audit = AuditWriter(mysql_engine)
    with mysql_engine.connect() as conn:
        entry = conn.execute(select(audit.table).where(audit.table.c.batch_id == batch_id)).one()
        commit = conn.execute(select(audit.commits).where(
            audit.commits.c.batch_id == batch_id
        )).one()
    assert entry.status == "success" and commit.rows_inserted == 1
    with pytest.raises(BatchConflictError):
        loader.load([candidate(name="changed")], batch_id, Customer, target)
    assert len(target_rows(mysql_engine, table_name)) == 1


def test_mysql8_audit_failure_rolls_back_data_and_ledger(mysql_engine, table_name, monkeypatch):
    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="append")
    batch = str(uuid4())
    original = AuditWriter.record_success

    def fail_audit(self, conn, result, source, counts):
        original(self, conn, result, source, counts)
        raise RuntimeError("mysql8 injected audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(AuditWriter, "record_success", fail_audit)
        with pytest.raises(RuntimeError, match="injected audit"):
            loader.load([candidate()], batch, Customer, target)
    assert target_rows(mysql_engine, table_name) == []
    audit = AuditWriter(mysql_engine)
    with mysql_engine.connect() as conn:
        assert conn.execute(select(audit.commits).where(
            audit.commits.c.batch_id == batch
        )).first() is None
        assert conn.execute(select(audit.table).where(
            audit.table.c.batch_id == batch
        )).first() is None


def test_mysql8_concurrent_scd2_updates_have_one_current(mysql_engine, table_name):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="scd2", key=["customer_id"],
                            tracked_columns=["full_name"], lock_timeout_seconds=30)
    loader.load([candidate()], str(uuid4()), Customer, target)
    start = Barrier(2)

    def worker(name):
        start.wait(timeout=20)
        return loader.load([candidate(name=name)], str(uuid4()), Customer, target)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, ["Alice Second", "Alice Third"]))
    assert all(count.loaded == 1 for count in results)
    assert sum(count.versions_created for count in results) == 2
    all_versions = target_rows(mysql_engine, table_name)
    assert len(all_versions) == 3
    assert sum(bool(row["is_current"]) for row in all_versions) == 1


def test_mysql8_scd2_unique_active_index_blocks_external_duplicate(mysql_engine, table_name):
    from sqlalchemy.exc import IntegrityError

    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="scd2", key=["customer_id"])
    loader.load([candidate()], str(uuid4()), Customer, target)
    table = Table(table_name, MetaData(), autoload_with=mysql_engine)
    with pytest.raises(IntegrityError):
        with mysql_engine.begin() as conn:
            conn.execute(table.insert().values(
                **candidate(name="Duplicate").data,
                __etl_id=str(uuid4()), effective_from=datetime.now(timezone.utc).replace(tzinfo=None),
                effective_to=None, is_current=True,
            ))
    assert len(target_rows(mysql_engine, table_name)) == 1


def test_mysql8_upgrade_legacy_scd2_requires_explicit_migration(mysql_engine, table_name):
    from sqlalchemy import Boolean, Column, String
    from sqlalchemy.dialects.mysql import DATETIME
    from etlguard.loaders.mysql import _model_columns

    meta = MetaData()
    Table(table_name, meta,
                   Column("__etl_id", String(36), primary_key=True), *_model_columns(Customer),
                   Column("effective_from", DATETIME(fsp=6), nullable=False),
                   Column("effective_to", DATETIME(fsp=6)),
                   Column("is_current", Boolean(), nullable=False), mysql_engine="InnoDB")
    meta.create_all(mysql_engine)
    loader = MysqlLoader(engine=mysql_engine)
    target = TargetSettings(table=table_name, mode="scd2", key=["customer_id"])
    with pytest.raises(ValueError, match="UNIQUE active-row guard"):
        loader.load([candidate()], str(uuid4()), Customer, target)
    loader.upgrade_scd2(Customer, target)
    assert loader.load([candidate()], str(uuid4()), Customer, target).inserted == 1
    assert len(target_rows(mysql_engine, table_name)) == 1


def test_distinct_target_writers_do_not_deadlock_shared_metadata(mysql_engine):
    """Synchronize missing metadata reads to expose REPEATABLE READ gap locks."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local
    from sqlalchemy import event
    from etlguard.loaders.mysql import build_tables

    audit = AuditWriter(mysql_engine)
    audit.init_db()
    targets = [TargetSettings(table='it_parallel_' + uuid4().hex[:10], mode='append')
               for _ in range(3)]
    for target in targets:
        table, _ = build_tables(Customer, target)
        table.metadata.create_all(mysql_engine)
    from uuid import UUID

    base_id = uuid4().int & ~0xffff
    batch_ids = [str(UUID(int=base_id + i)) for i in range(3)]
    ledger_barrier = Barrier(3)
    audit_barrier = Barrier(3)
    state = local()

    def synchronize(conn, cursor, statement, params, context, many):
        if statement.startswith('SELECT etl_batch_commits.') and 'WHERE' in statement:
            if not getattr(state, 'ledger_seen', False):
                state.ledger_seen = True
                ledger_barrier.wait(timeout=10)
        elif statement.startswith('SELECT etl_run_audit.') and 'WHERE' in statement:
            if not getattr(state, 'audit_seen', False):
                state.audit_seen = True
                audit_barrier.wait(timeout=10)

    event.listen(mysql_engine, 'after_cursor_execute', synchronize)
    try:
        def worker(item):
            target, batch_id = item
            return MysqlLoader(engine=mysql_engine).load(
                [candidate()], batch_id, Customer, target)
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(worker, zip(targets, batch_ids, strict=True)))
        assert all(result.loaded == 1 for result in results)
        assert all(len(target_rows(mysql_engine, target.table)) == 1 for target in targets)
    finally:
        event.remove(mysql_engine, 'after_cursor_execute', synchronize)
        with mysql_engine.begin() as conn:
            for target in targets:
                for name in [f'stg_{target.table}', target.table]:
                    Table(name, MetaData(), autoload_with=conn).drop(conn)
