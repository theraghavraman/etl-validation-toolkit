"""MySQL 8 loader: transactional batch staging, DML merge, type-aware DDL."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from types import SimpleNamespace
from uuid import UUID
from decimal import Decimal
from typing import Any, Sequence, get_args, get_origin
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import (
    BigInteger, Boolean, Column, Computed, Date, Float, Index, MetaData,
    Numeric, SmallInteger, String, Table, UniqueConstraint, create_engine, func, inspect,
    select, text, tuple_,
)
from sqlalchemy.dialects.mysql import DATETIME, insert as mysql_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import SchemaItem

from etlguard.audit import AuditWriter, BatchConflictError, persisted_counts
from etlguard.config import IDENTIFIER, TargetSettings
from etlguard.reconcile import LoadCounts, reconcile
from etlguard.scd2 import scd2_merge
from etlguard.validation import Candidate


def sqlalchemy_type(annotation: Any) -> Any:
    """Map concrete Pydantic field types to portable SQLAlchemy column types."""
    import types
    import typing

    origin = get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(non_none) == 1:
            return sqlalchemy_type(non_none[0])
    if origin is typing.Literal:
        return sqlalchemy_type(type(get_args(annotation)[0]))
    if annotation is str:
        return String(255)
    if annotation is int:
        return BigInteger()
    if annotation is float:
        return Float()
    if annotation is bool:
        return Boolean()
    if annotation is Decimal:
        return Numeric(18, 2)
    if annotation is date:
        return Date()
    if annotation is datetime:
        return DATETIME(fsp=6)
    raise ValueError(f"Unsupported model field type for MySQL: {annotation}")


def _model_columns(model: type[BaseModel]) -> list[Column[Any]]:
    cols = []
    for name, info in model.model_fields.items():
        if (not IDENTIFIER.fullmatch(name) or name.startswith("__etl_") or
                name in {"effective_from", "effective_to", "is_current"}):
            raise ValueError(f"Unsafe or reserved model column name: {name}")
        if info.annotation is None:
            raise ValueError(f"Field {name} has no type")
        nullable = not info.is_required() or type(None) in get_args(info.annotation)
        column_type = sqlalchemy_type(info.annotation)
        if isinstance(column_type, String) and info.metadata:
            for constraint in info.metadata:
                max_length = getattr(constraint, "max_length", None)
                if isinstance(max_length, int) and max_length > 0:
                    column_type = String(max_length)
        cols.append(Column(name, column_type, nullable=nullable))
    return cols


def build_tables(model: type[BaseModel], target: TargetSettings) -> tuple[Table, Table]:
    fields = set(model.model_fields)
    if any(key not in fields for key in [*target.key, *target.tracked_columns]):
        raise ValueError("target.key and target.tracked_columns must exist on the Pydantic model")
    meta = MetaData()
    unique = [UniqueConstraint(*target.key, name=f"uq_{target.table}_business")] if (
        target.key and target.mode != "scd2"
    ) else []
    scd_items: list[SchemaItem] = [
            Column("effective_from", DATETIME(fsp=6), nullable=False),
            Column("effective_to", DATETIME(fsp=6)),
            Column("is_current", Boolean(), nullable=False),
            # UNIQUE(key, marker) allows many NULL markers for historical rows,
            # but exactly one marker=1 for an active version (MySQL 8 + SQLite).
            Column("__etl_current_marker", SmallInteger(), Computed(
                "CASE WHEN is_current = 1 THEN 1 ELSE NULL END", persisted=True,
            )),
            UniqueConstraint(*target.key, "__etl_current_marker",
                             name=f"uq_{target.table}_active"),
            Index(f"ix_{target.table}_current", *target.key, "is_current"),
    ] if target.mode == "scd2" else []
    target_table = Table(
        target.table, meta,
        Column("__etl_id", String(36), primary_key=True),
        *_model_columns(model),
        *scd_items,
        *unique,
        mysql_engine="InnoDB",
    )
    stage_name = f"stg_{target.table}"
    stage_table = Table(
        stage_name, meta,
        Column("__etl_id", String(36), primary_key=True),
        Column("__etl_batch_id", String(36), nullable=False),
        Column("__etl_source_row_number", BigInteger(), nullable=False),
        Column("__etl_ingested_at", DATETIME(fsp=6), nullable=False),
        *_model_columns(model),
        Index(f"ix_{stage_name}_batch", "__etl_batch_id"),
        mysql_engine="InnoDB",
    )
    return target_table, stage_table


def batches(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


class MysqlLoader:
    def __init__(self, database_url: str | None = None, engine: Engine | None = None):
        if engine is None:
            if not database_url or not database_url.startswith("mysql+pymysql://"):
                raise ValueError("Supply a mysql+pymysql:// database URL")
            engine = create_engine(database_url, pool_pre_ping=True)
        self.engine = engine

    @contextmanager
    def _locked_connection(self, target: TargetSettings):
        """Serialize every writer to a target, including FIRST SCD2 inserts.

        MySQL GET_LOCK is connection-scoped and must be released explicitly.
        The lock spans all transactional work, not just the SCD2 row SELECT.
        """
        with self.engine.connect() as conn:
            lock = f"etlguard:table:{target.table}"
            acquired = False
            if conn.dialect.name == "mysql":
                value = conn.execute(text("SELECT GET_LOCK(:name, :timeout)"), {
                    "name": lock, "timeout": target.lock_timeout_seconds,
                }).scalar()
                conn.commit()
                if value != 1:
                    raise TimeoutError(f"Could not acquire MySQL ETL lock {lock!r}")
                acquired = True
            try:
                yield conn
            finally:
                if acquired:
                    try:
                        if conn.in_transaction():
                            conn.rollback()
                        released = conn.execute(text("SELECT RELEASE_LOCK(:name)"),
                                                {"name": lock}).scalar()
                        conn.commit()
                        if released != 1:
                            raise RuntimeError(f"MySQL ETL lock {lock!r} was not released")
                    except Exception:
                        conn.invalidate()  # Never return a lock-holding session to the pool.
                        raise

    @staticmethod
    def _digest(candidates: list[Candidate], model: type[BaseModel],
                target: TargetSettings) -> str:
        payload = {
            "model": f"{model.__module__}:{model.__qualname__}",
            "target": target.model_dump(mode="json"),
            "rows": [item.data for item in candidates],
        }
        return sha256(json.dumps(payload, sort_keys=True, default=str,
                                 separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_id(batch_id: str, request_hash: str) -> None:
        try:
            if str(UUID(batch_id)) != batch_id:
                raise ValueError("non-canonical UUID")
        except ValueError as exc:
            raise ValueError("batch_id must be a canonical UUID") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", request_hash):
            raise ValueError("request_hash must be a lowercase SHA-256 hex digest")

    @staticmethod
    def _check_replay(row: Any, batch_id: str, request_hash: str,
                      target: TargetSettings, pipeline: str) -> LoadCounts:
        if (row["request_hash"] != request_hash or row["target_table"] != target.table
                or row["target_mode"] != target.mode or row["pipeline"] != pipeline):
            raise BatchConflictError(
                f"Batch {batch_id} was committed for different input/configuration/target; "
                "use a new UUID for a different load, or restore the original input to retry"
            )
        return persisted_counts(row)

    def lookup_commit(self, batch_id: str, request_hash: str, target: TargetSettings,
                      pipeline: str) -> LoadCounts | None:
        """Recover the durable outcome after a lost connection/response at COMMIT."""
        audit = AuditWriter(self.engine)
        if not inspect(self.engine).has_table(audit.commits.name):
            return None
        with self.engine.connect() as conn:
            row = conn.execute(select(audit.commits).where(
                audit.commits.c.batch_id == batch_id
            )).mappings().first()
        return self._check_replay(row, batch_id, request_hash, target, pipeline) if row else None

    def _check_transactional_storage(self, table_names: list[str]) -> None:
        """MyISAM tables silently break rollback and atomic success guarantees."""
        if self.engine.dialect.name != "mysql":
            return
        inspector = inspect(self.engine)
        for name in table_names:
            options = inspector.get_table_options(name)
            engine_name = options.get("mysql_engine", "").lower()
            if engine_name != "innodb":
                raise ValueError(
                    f"Table {name} uses storage engine {engine_name or 'unknown'}; "
                    "atomic ETL loads require InnoDB for ALL target/stage/audit/ledger tables"
                )

    def _check_schema(self, model: type[BaseModel], target: TargetSettings,
                      target_table: Table, stage_table: Table) -> None:
        fields = list(model.model_fields)
        for table in (target_table, stage_table):
            missing = [name for name in fields if name not in table.c]
            if missing:
                raise ValueError(f"Existing table {table.name} missing model columns: {missing}")
        if target.mode not in {"upsert", "scd2"}:
            return
        inspector = inspect(self.engine)
        uniques: list[Sequence[str | None]] = [constraint.get("column_names") or []
                   for constraint in inspector.get_unique_constraints(target.table)]
        uniques += [index.get("column_names") or [] for index in inspector.get_indexes(target.table)
                    if index.get("unique")]
        uniques += [inspector.get_pk_constraint(target.table).get("constrained_columns") or []]
        if any(target_table.c[key].nullable for key in target.key):
            raise ValueError(
                f"{target.mode} business keys must be NOT NULL; MySQL UNIQUE keys "
                "permit duplicate NULL values"
            )
        if target.mode == "scd2":
            required = set([*target.key, "__etl_current_marker"])
            if ("__etl_current_marker" not in target_table.c or
                    target_table.c["__etl_current_marker"].computed is None or
                    not any(set(keys) == required and len(keys) == len(required)
                            for keys in uniques)):
                raise ValueError(
                    f"Existing SCD2 table {target.table} lacks its UNIQUE active-row guard; "
                    "run 'etlguard upgrade-scd2 path/to/pipeline.yaml' during maintenance"
                )
            return
        business_key = set(target.key)
        if not any(set(keys) == business_key and len(keys) == len(target.key) for keys in uniques):
            raise ValueError(
                f"Upsert target {target.table} has no UNIQUE key on {target.key}; "
                "add a unique index or use a fresh target table"
            )
        competing = [keys for keys in uniques
                     if keys and set(keys) not in (business_key, {"__etl_id"})]
        if competing:
            raise ValueError(
                f"Upsert target {target.table} has competing UNIQUE keys {competing}; "
                "ON DUPLICATE KEY UPDATE could change a different business record"
            )

    def load(
        self, candidates: list[Candidate], batch_id: str,
        model: type[BaseModel], target: TargetSettings, *,
        request_hash: str | None = None, audit_result: Any = None,
        allow_empty_replace: bool = False,
    ) -> LoadCounts:
        """Commit data, unique batch marker and success audit in ONE DB transaction.

        An identical batch_id+hash replays recorded counts without touching data.
        A changed feed/configuration using that UUID is rejected, not reloaded.
        """
        request_hash = request_hash or self._digest(candidates, model, target)
        self._validate_id(batch_id, request_hash)
        if audit_result is None:
            # Low-level callers get the SAME atomic audit guarantee as Pipeline.run.
            current = datetime.now(timezone.utc)
            audit_result = SimpleNamespace(
                batch_id=batch_id, pipeline="direct_api", source="direct_api",
                started_at=current, rows_read=len(candidates), rows_valid=len(candidates),
                rows_rejected=0, rows_loaded=0, error_counts_by_type={},
                error_counts_by_field={}, dataset_issues=[], failures=[],
            )
        pipeline = audit_result.pipeline
        if target.mode == "truncate_insert" and not candidates and not allow_empty_replace:
            raise ValueError("truncate_insert blocked: no valid records; explicit approval is required")
        if target.mode in {"upsert", "scd2"}:
            keys = [tuple(item.data[key] for key in target.key) for item in candidates]
            if len(set(keys)) != len(keys):
                raise ValueError("Duplicate business keys within one load are not allowed")
        # DDL must occur BEFORE the data transaction; audit schema stays v0.1.1-compatible.
        auditor = AuditWriter(self.engine)
        auditor.init_db()
        target_def, _stage_def = build_tables(model, target)
        try:
            with self._locked_connection(target) as conn:
                target_def.metadata.create_all(conn, checkfirst=True)
                conn.commit()  # MySQL CREATE TABLE can implicitly commit.
                target_table = Table(target.table, MetaData(), autoload_with=conn)
                stage_table = Table(f"stg_{target.table}", MetaData(), autoload_with=conn)
                self._check_schema(model, target, target_table, stage_table)
                self._check_transactional_storage([
                    target.table, stage_table.name, auditor.table.name, auditor.commits.name,
                ])
                # Reflections/inspections may implicitly start a read transaction.
                conn.commit()
                with conn.begin():
                    previous = conn.execute(select(auditor.commits).where(
                        auditor.commits.c.batch_id == batch_id
                    ).with_for_update()).mappings().first()
                    if previous:
                        return self._check_replay(previous, batch_id, request_hash,
                                                  target, pipeline)
                    # Unique reservation is not visible until data + audit commit;
                    # concurrent reuse of this UUID blocks or fails without double loading.
                    conn.execute(auditor.commits.insert().values(
                        batch_id=batch_id, request_hash=request_hash, pipeline=pipeline,
                        target_table=target.table, target_mode=target.mode,
                        committed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        rows_inserted=0, rows_updated=0, rows_unchanged=0,
                        scd2_versions_created=0, rows_valid=len(candidates),
                    ))
                    stage_rows = [{
                        "__etl_id": str(uuid4()),
                        "__etl_batch_id": batch_id,
                        "__etl_source_row_number": item.lineage.source_row_number,
                        "__etl_ingested_at": item.lineage.ingested_at.replace(tzinfo=None),
                        **item.data,
                    } for item in candidates]
                    conn.execute(stage_table.delete().where(
                        stage_table.c["__etl_batch_id"] == batch_id
                    ))
                    for chunk in batches(stage_rows, target.batch_size):
                        conn.execute(stage_table.insert(), chunk)
                    staged = conn.execute(select(stage_table).where(
                        stage_table.c["__etl_batch_id"] == batch_id
                    )).mappings().all()
                    fields = list(model.model_fields)
                    records = [{name: row[name] for name in fields} for row in staged]
                    if target.mode == "truncate_insert" and target.min_replace_ratio is not None:
                        previous_rows = conn.execute(select(func.count()).select_from(target_table)).scalar_one()
                        if previous_rows and len(records) / previous_rows < target.min_replace_ratio:
                            raise ValueError(
                                f"truncate_insert blocked: {len(records)} valid rows vs "
                                f"{previous_rows} existing rows below min_replace_ratio="
                                f"{target.min_replace_ratio:.2f}"
                            )
                    counts = self._merge(conn, target_table, records, target)
                    reconcile(len(candidates), counts)
                    conn.execute(stage_table.delete().where(
                        stage_table.c["__etl_batch_id"] == batch_id
                    ))
                    conn.execute(auditor.commits.update().where(
                        auditor.commits.c.batch_id == batch_id
                    ).values(committed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                             rows_inserted=counts.inserted, rows_updated=counts.updated,
                             rows_unchanged=counts.unchanged,
                             scd2_versions_created=counts.versions_created))
                    auditor.record_success(conn, audit_result, audit_result.source, counts)
                    return counts
        except Exception:
            # The connection can be lost while a COMMIT reply is in flight.
            # Only a durable matching commit proves success; otherwise fail closed.
            try:
                recovered = self.lookup_commit(batch_id, request_hash, target, pipeline)
            except BatchConflictError:
                raise  # Distinguish a changed batch from a transient database error.
            except Exception:
                recovered = None  # Preserve the original failure (commit state unknown).
            if recovered is not None:
                return recovered
            raise

    def upgrade_scd2(self, model: type[BaseModel], target: TargetSettings) -> None:
        """Explicit, operator-approved MySQL 8 migration for pre-0.1.2 SCD2 tables.

        Invoke in a maintenance window. It is NOT called by ordinary loads.
        """
        if self.engine.dialect.name != "mysql" or target.mode != "scd2":
            raise ValueError("upgrade-scd2 requires MySQL 8 and an SCD2 target")
        build_tables(model, target)  # Validate model and identifiers.
        inspector = inspect(self.engine)
        if not inspector.has_table(target.table):
            raise ValueError(f"SCD2 table {target.table} does not exist")
        with self._locked_connection(target) as conn:
            table = Table(target.table, MetaData(), autoload_with=conn)
            if any(key not in table.c or table.c[key].nullable for key in target.key):
                raise ValueError("SCD2 key columns must exist and be NOT NULL before migration")
            if "is_current" not in table.c:
                raise ValueError("SCD2 table is missing is_current")
            active = select(*[table.c[key] for key in target.key]).where(
                table.c.is_current.is_(True)
            ).group_by(*(table.c[key] for key in target.key)).having(func.count() > 1).limit(1)
            if conn.execute(active).first() is not None:
                raise ValueError("Cannot add active-row uniqueness: duplicate active SCD2 keys exist")
            conn.commit()
            if "__etl_current_marker" not in table.c:
                conn.execute(text(
                    f"ALTER TABLE `{target.table}` ADD COLUMN `__etl_current_marker` "
                    "TINYINT GENERATED ALWAYS AS (CASE WHEN is_current = 1 THEN 1 ELSE NULL END) STORED"
                ))
                conn.commit()  # Explicit DDL migration; MySQL auto-commits anyway.
            inspector = inspect(conn)
            unique_sets = [set(c.get("column_names") or [])
                           for c in inspector.get_unique_constraints(target.table)]
            unique_sets.extend(set(i.get("column_names") or [])
                               for i in inspector.get_indexes(target.table) if i.get("unique"))
            wanted = set([*target.key, "__etl_current_marker"])
            if wanted not in unique_sets:
                fields = ", ".join(f"`{key}`" for key in [*target.key, "__etl_current_marker"])
                conn.execute(text(
                    f"ALTER TABLE `{target.table}` ADD UNIQUE INDEX "
                    f"`uq_{target.table}_active` ({fields})"
                ))
                conn.commit()

    def _merge(
        self, conn: Connection, table: Table,
        records: list[dict[str, Any]], target: TargetSettings,
    ) -> LoadCounts:
        if target.mode == "truncate_insert":
            # Transactional DELETE, not MySQL TRUNCATE (TRUNCATE implicitly commits).
            conn.execute(table.delete())
        if not records:
            return LoadCounts(0, 0)
        if target.mode == "scd2":
            return scd2_merge(conn, table, records, target.key, target.tracked_columns)
        if target.mode in {"append", "truncate_insert"}:
            for chunk in batches(records, target.batch_size):
                conn.execute(table.insert(), [{"__etl_id": str(uuid4()), **row} for row in chunk])
            return LoadCounts(len(records), 0)
        existing: set[tuple[Any, ...]] = set()
        for chunk in batches(records, target.batch_size):
            keys = [tuple(row[key] for key in target.key) for row in chunk]
            if len(target.key) == 1:
                condition = table.c[target.key[0]].in_([value[0] for value in keys])
            else:
                condition = tuple_(*(table.c[key] for key in target.key)).in_(keys)
            selected = conn.execute(select(*(table.c[key] for key in target.key)).where(condition))
            existing.update(tuple(row) for row in selected)
        for chunk in batches(records, target.batch_size):
            values = [{"__etl_id": str(uuid4()), **row} for row in chunk]
            statement = mysql_insert(table).values(values)
            update_fields = [name for name in chunk[0] if name not in target.key]
            if not update_fields:
                update_fields = target.key[:1]
            conn.execute(statement.on_duplicate_key_update(**{
                name: statement.inserted[name] for name in update_fields
            }))
        updated = sum(tuple(row[key] for key in target.key) in existing for row in records)
        return LoadCounts(len(records) - updated, updated)
