"""End-to-end pipeline orchestration, policies, summaries, audit and lazy DB loading."""

from __future__ import annotations

import importlib
import json
from hashlib import sha256
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from etlguard.config import ConfigError, PipelineSettings, load_config
from etlguard.core import CoreResult, ValidationEngine
from etlguard.rejects import RejectWriter
from etlguard.sources import ApiSource, CsvSource, ExcelSource


class RunResult(BaseModel):
    batch_id: str
    pipeline: str
    source: str
    status: Literal["success", "dry_run", "failed"] = "failed"
    request_hash: str | None = None
    replayed: bool = False
    durable_commit: bool = False
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = 0
    rows_read: int = 0
    rows_cleaned: int = 0
    rows_valid: int = 0
    rows_rejected: int = 0
    rows_loaded: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    scd2_versions_created: int = 0
    duplicates_dropped: int = 0
    error_counts_by_type: dict[str, int] = Field(default_factory=dict)
    error_counts_by_field: dict[str, int] = Field(default_factory=dict)
    dataset_issues: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    reject_csv: str | None = None
    reject_json: str | None = None
    summary_json: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"success", "dry_run"}


def resolve_model(reference: str) -> type[BaseModel]:
    if ":" not in reference:
        raise ConfigError("model must be a module.path:ClassName reference")
    module_name, class_name = reference.split(":", maxsplit=1)
    try:
        imported = importlib.import_module(module_name)
        model = getattr(imported, class_name)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(f"Cannot import Pydantic model: {reference}: {exc}") from exc
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise ConfigError(f"{reference} must be a Pydantic BaseModel subclass")
    return model


class Pipeline:
    def __init__(self, settings: PipelineSettings, base_dir: Path):
        self.settings = settings
        self.base_dir = base_dir

    @classmethod
    def from_yaml(cls, path: str | Path) -> Pipeline:
        settings, base_dir = load_config(path)
        return cls(settings, base_dir)

    def _source(self) -> CsvSource | ExcelSource | ApiSource:
        cfg = self.settings.source
        if cfg.type == "csv":
            return CsvSource(cfg, self.base_dir)
        if cfg.type == "excel":
            return ExcelSource(cfg, self.base_dir)
        return ApiSource(cfg)

    def run(self, dry_run: bool = False, batch_id: str | None = None) -> RunResult:
        if batch_id is not None:
            try:
                if str(UUID(batch_id)) != batch_id:
                    raise ValueError("non-canonical UUID")
            except ValueError as exc:
                raise ConfigError("--batch-id must be a canonical UUID") from exc
        start = perf_counter()
        now = datetime.now(timezone.utc)
        cfg = self.settings
        source = cfg.source.name or cfg.source.path or cfg.source.url or "unknown"
        result = RunResult(
            batch_id=batch_id or str(uuid4()), pipeline=cfg.pipeline, source=source,
            started_at=now, finished_at=now,
        )
        prefix = (self.base_dir / cfg.output.directory).resolve() / f"{cfg.pipeline}_{result.batch_id}"
        core = CoreResult()
        engine: Any = None
        try:
            model = resolve_model(cfg.model)
            checks = cfg.checks.model_copy(deep=True)
            if cfg.target is not None and cfg.target.mode in {"upsert", "scd2"} and not checks.unique:
                checks.unique = cfg.target.key[:]
            core = ValidationEngine(model, cfg.cleaning, checks, self.base_dir).run(
                self._source().read(result.batch_id)
            )
            result.rows_read = core.rows_read
            # Repeatable hash of the complete incoming feed + validated settings.
            # This prevents a changed input/configuration silently reusing a batch UUID.
            digest_input = {
                "config": cfg.model_dump(mode="json"),
                "model_schema": model.model_json_schema(),
                "source_digest": core.source_digest,
            }
            result.request_hash = sha256(json.dumps(
                digest_input, sort_keys=True, default=str, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            result.rows_cleaned = core.rows_cleaned
            result.rows_valid = core.rows_valid
            result.rows_rejected = core.rows_rejected
            result.duplicates_dropped = core.duplicates_dropped
            result.error_counts_by_type = core.error_counts_by_type
            result.error_counts_by_field = core.error_counts_by_field
            result.dataset_issues = [f"{issue.field}: {issue.message}" for issue in core.dataset_issues]
            if core.dataset_issues:
                result.failures.append("dataset-level validation failed")
            if core.source_error is not None:
                result.failures.append(core.source_error)
            max_rate = cfg.policy.fail_if_reject_rate_above
            if max_rate is not None and result.rows_read and result.rows_rejected / result.rows_read > max_rate:
                result.failures.append(
                    f"reject rate {result.rows_rejected / result.rows_read:.2%} exceeds {max_rate:.2%}"
                )
            if cfg.target is not None and cfg.target.mode == "truncate_insert":
                if result.rows_rejected and not cfg.policy.allow_partial_replace:
                    result.failures.append(
                        "truncate_insert blocked: rejected source records; "
                        "set policy.allow_partial_replace=true to explicitly allow this"
                    )
                if result.rows_read == 0 and not cfg.policy.allow_empty_replace:
                    result.failures.append(
                        "truncate_insert blocked: source is empty; "
                        "set policy.allow_empty_replace=true to explicitly allow this"
                    )
            if result.failures:
                result.status = "failed"
            elif dry_run:
                result.status = "dry_run"
            else:
                if cfg.target is None:
                    raise ConfigError("etlguard run requires target settings")
                db_url = os.getenv(cfg.database_url_env)
                if not db_url:
                    raise ConfigError(f"Set database URL environment variable: {cfg.database_url_env}")
                from etlguard.loaders.mysql import MysqlLoader

                loader = MysqlLoader(db_url)
                engine = loader.engine
                counts = loader.load(
                    core.valid, result.batch_id, model, cfg.target,
                    request_hash=result.request_hash, audit_result=result,
                    allow_empty_replace=cfg.policy.allow_empty_replace,
                )
                result.replayed = counts.replayed
                result.durable_commit = True
                result.rows_inserted = counts.inserted
                result.rows_updated = counts.updated
                result.rows_unchanged = counts.unchanged
                result.scd2_versions_created = counts.versions_created
                result.rows_loaded = counts.loaded
                result.status = "success"
        except ConfigError:
            raise
        except Exception as exc:
            # DB errors must yield a failed job, not partial uncommitted data.
            result.status = "failed"
            result.failures.append(f"{type(exc).__name__}: {exc}")
        finally:
            result.finished_at = datetime.now(timezone.utc)
            result.duration_seconds = round(perf_counter() - start, 4)
            if not dry_run and result.status != "success":
                try:
                    if engine is None and os.getenv(cfg.database_url_env):
                        from etlguard.loaders.mysql import MysqlLoader
                        engine = MysqlLoader(os.environ[cfg.database_url_env]).engine
                    if engine is not None:
                        from etlguard.audit import AuditWriter
                        AuditWriter(engine).record_failure(result, source)
                except Exception as exc:
                    result.failures.append(f"failure audit unavailable: {type(exc).__name__}: {exc}")
            try:
                csv_path, json_path = RejectWriter().write(prefix, core.rejects)
                result.reject_csv = str(csv_path)
                result.reject_json = str(json_path)
            except Exception as exc:
                # The DB success marker is authoritative. A local file I/O error
                # must NOT transform an already-committed batch into a retryable load.
                message = f"reject report unavailable: {type(exc).__name__}: {exc}"
                if result.durable_commit:
                    result.warnings.append(message)
                else:
                    result.status = "failed"
                    result.failures.append(message)
            summary_path = prefix.with_name(prefix.name + "_summary.json")
            try:
                result.summary_json = str(summary_path)
                summary_path.parent.mkdir(parents=True, exist_ok=True)
                summary_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            except Exception as exc:
                message = f"summary report unavailable: {type(exc).__name__}: {exc}"
                if result.durable_commit:
                    result.warnings.append(message)
                else:
                    result.status = "failed"
                    result.failures.append(message)
                result.summary_json = None
            if engine is not None:
                engine.dispose()
        return result
