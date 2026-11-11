"""Durable ETL audits and an atomic, idempotent batch-commit ledger."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Column, MetaData, String, Table, Text, select
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import Connection, Engine

from etlguard.reconcile import LoadCounts


def audit_table() -> Table:
    """Keep the v0.1.1 audit schema compatible with existing installations."""
    return Table(
        "etl_run_audit", MetaData(),
        Column("batch_id", String(36), primary_key=True),
        Column("pipeline", String(128), nullable=False),
        Column("source", String(512), nullable=False),
        Column("started_at", DATETIME(fsp=6), nullable=False),
        Column("finished_at", DATETIME(fsp=6), nullable=False),
        Column("status", String(32), nullable=False),
        Column("rows_read", BigInteger(), nullable=False),
        Column("rows_valid", BigInteger(), nullable=False),
        Column("rows_rejected", BigInteger(), nullable=False),
        Column("rows_loaded", BigInteger(), nullable=False),
        Column("error_summary", Text(), nullable=False),
        mysql_engine="InnoDB",
    )


def commits_table() -> Table:
    """Rows become visible only if target data and the audit commit together."""
    return Table(
        "etl_batch_commits", MetaData(),
        Column("batch_id", String(36), primary_key=True),
        Column("request_hash", String(64), nullable=False),
        Column("pipeline", String(128), nullable=False),
        Column("target_table", String(64), nullable=False),
        Column("target_mode", String(32), nullable=False),
        Column("committed_at", DATETIME(fsp=6), nullable=False),
        Column("rows_inserted", BigInteger(), nullable=False),
        Column("rows_updated", BigInteger(), nullable=False),
        Column("rows_unchanged", BigInteger(), nullable=False),
        Column("scd2_versions_created", BigInteger(), nullable=False),
        Column("rows_valid", BigInteger(), nullable=False),
        mysql_engine="InnoDB",
    )


def persisted_counts(row: Any) -> LoadCounts:
    return LoadCounts(
        inserted=row["rows_inserted"], updated=row["rows_updated"],
        unchanged=row["rows_unchanged"], versions_created=row["scd2_versions_created"],
        replayed=True,
    )


class BatchConflictError(ValueError):
    """A batch ID already belongs to a different source/configuration/target."""


class AuditWriter:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.table = audit_table()
        self.commits = commits_table()

    def init_db(self) -> None:
        # Both DDL calls must happen *outside* the data transaction (MySQL DDL commits).
        self.table.metadata.create_all(self.engine, checkfirst=True)
        self.commits.metadata.create_all(self.engine, checkfirst=True)

    def _values(self, result: Any, source: str, status: str, counts: LoadCounts | None = None) -> dict[str, Any]:
        return {
            "batch_id": result.batch_id,
            "pipeline": result.pipeline,
            "source": source[:512],
            "started_at": result.started_at.replace(tzinfo=None),
            "finished_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "status": status,
            "rows_read": result.rows_read,
            "rows_valid": result.rows_valid,
            "rows_rejected": result.rows_rejected,
            "rows_loaded": counts.loaded if counts else result.rows_loaded,
            "error_summary": json.dumps({
                "error_counts_by_type": result.error_counts_by_type,
                "error_counts_by_field": result.error_counts_by_field,
                "issues": result.dataset_issues,
                "failures": result.failures,
            }),
        }

    def record_success(self, conn: Connection, result: Any, source: str, counts: LoadCounts) -> None:
        """Write success on the SAME transaction/connection as the target merge."""
        values = self._values(result, source, "success", counts)
        existing = conn.execute(
            select(self.table.c.status).where(self.table.c.batch_id == result.batch_id)
            .with_for_update()
        ).first()
        if existing is None:
            conn.execute(self.table.insert().values(**values))
        elif existing.status != "success":
            conn.execute(self.table.update().where(self.table.c.batch_id == result.batch_id)
                         .values(**values))
        else:
            raise BatchConflictError("Audit already claims a successful batch without a new commit")

    def record_failure(self, result: Any, source: str) -> None:
        """Best-effort failure audit, never overwriting a committed success."""
        self.init_db()
        values = self._values(result, source, "failed")
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(self.table.c.status).where(self.table.c.batch_id == result.batch_id)
                .with_for_update()
            ).first()
            if existing is None:
                conn.execute(self.table.insert().values(**values))
            elif existing.status != "success":
                conn.execute(self.table.update().where(self.table.c.batch_id == result.batch_id)
                             .values(**values))

    def write(self, result: Any, source: str) -> None:
        """Backward-compatible one-off audit API; not for transactional load success."""
        self.init_db()
        with self.engine.begin() as conn:
            conn.execute(self.table.insert().values(**self._values(result, source, result.status)))
