#!/usr/bin/env python3
"""Independent read-only, row-level verification of an isolated MySQL 8 shadow pilot.

The CLI refuses non-local, non-disposable MySQL URLs. A separate passing MySQL 8
staging report is mandatory. No source records, secrets, or database rows are
included in the JSON evidence; only counts, fingerprints and mismatch types.

Offline unit tests exercise compare_shadow() against SQLite. This does NOT
substitute for executing the CLI against genuine disposable MySQL 8 staging.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from math import isfinite
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.engine import Engine

from etlguard.config import load_config
from etlguard.core import ValidationEngine
from etlguard.pipeline import Pipeline, RunResult, resolve_model
from etlguard.validation import Candidate
from assess_promotion import check_staging
from prepare_shadow_pilot import SHADOW_ENV, sha256_file, verify
from run_shadow_pilot import check_shadow_destination, preflight_shadow


def canonical(value: Any) -> Any:
    """Stable JSON values for typed MySQL / SQLite comparison; no raw output."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite numeric values cannot be reconciled")
        return str(value.normalize())
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("Non-finite floating-point values cannot be reconciled")
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    raise ValueError(f"Unsupported database value type for reconciliation: {type(value).__name__}")


def serialized(data: dict[str, Any]) -> str:
    return json.dumps(canonical(data), separators=(",", ":"), sort_keys=True, ensure_ascii=True)


def row_fingerprint(rows: list[dict[str, Any]]) -> str:
    """Physical fingerprint includes ETL IDs and SCD2 history, not just counts."""
    ordered = sorted(serialized(row) for row in rows)
    digest = sha256()
    for value in ordered:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def compare_shadow(engine: Engine, settings: Any, model: Any, batch_id: str,
                   expected: list[Candidate], dry: RunResult) -> dict[str, Any]:
    """Read-only evidence for one first-load + any identical retries.

    All SQL statements are SELECT. This function can be tested with SQLite,
    while the CLI restricts actual operator execution to an isolated MySQL 8.
    """
    target = settings.target
    if target is None:
        raise ValueError("Shadow target must be configured")
    names = [target.table, f"stg_{target.table}", "etl_batch_commits", "etl_run_audit"]
    problems: list[str] = []
    with engine.connect() as conn:
        inspector = inspect(conn)
        absent = [name for name in names if not inspector.has_table(name)]
        if absent:
            raise ValueError("Shadow tables missing: " + ", ".join(absent))
        target_t, stage_t, ledger_t, audit_t = (
            Table(name, MetaData(), autoload_with=conn) for name in names
        )
        target_rows = [dict(item) for item in conn.execute(select(target_t)).mappings()]
        stage_rows = conn.execute(select(stage_t)).all()
        ledger_rows = conn.execute(select(ledger_t)).mappings().all()
        audit_rows = conn.execute(select(audit_t)).mappings().all()

    # Never put raw source or persisted data into the returned evidence.
    if stage_rows:
        problems.append("staging_rows_remain")
    if len(ledger_rows) != 1 or ledger_rows[0]["batch_id"] != batch_id:
        problems.append("ledger_batch_count_or_identity_mismatch")
    if len(audit_rows) != 1 or audit_rows[0]["batch_id"] != batch_id:
        problems.append("audit_batch_count_or_identity_mismatch")
    if len(target_rows) != len(expected):
        problems.append("target_row_count_mismatch")
    if len(expected) == 0:
        problems.append("no_valid_source_rows_for_shadow_pilot")

    active_rows = target_rows
    if target.mode == "scd2":
        active_rows = [row for row in target_rows if bool(row.get("is_current"))]
        if len(active_rows) != len(target_rows):
            problems.append("unexpected_scd2_history_during_first_batch")
        if any(row.get("effective_to") is not None for row in active_rows):
            problems.append("active_scd2_row_has_end_timestamp")
        keys = [tuple(row[key] for key in target.key) for row in active_rows]
        if len(keys) != len(set(keys)):
            problems.append("multiple_active_scd2_versions")

    # Round-trip through the declared model to normalize SQL date/decimal columns.
    fields = list(model.model_fields)
    try:
        observed_data = [
            model.model_validate({name: row[name] for name in fields}).model_dump(mode="python")
            for row in active_rows
        ]
        expected_data = [model.model_validate(item.data).model_dump(mode="python")
                         for item in expected]
        actual_counter = Counter(serialized(item) for item in observed_data)
        expected_counter = Counter(serialized(item) for item in expected_data)
        missing_count = sum((expected_counter - actual_counter).values())
        unexpected_count = sum((actual_counter - expected_counter).values())
        if missing_count or unexpected_count:
            problems.append("source_vs_persisted_row_values_mismatch")
    except (ValueError, KeyError, TypeError):
        # Avoid exposing data through Pydantic's value-bearing validation errors.
        problems.append("persisted_row_schema_or_type_mismatch")
        missing_count = len(expected)
        unexpected_count = len(active_rows)

    if len(ledger_rows) == 1:
        commit = ledger_rows[0]
        ledger_rules = {
            "request_hash": dry.request_hash,
            "pipeline": settings.pipeline,
            "target_table": target.table,
            "target_mode": target.mode,
            "rows_valid": dry.rows_valid,
            "rows_inserted": dry.rows_valid,
            "rows_updated": 0,
            "rows_unchanged": 0,
            "scd2_versions_created": 0,
        }
        if any(commit.get(key) != value for key, value in ledger_rules.items()):
            problems.append("batch_commit_ledger_mismatch")
    if len(audit_rows) == 1:
        audit = audit_rows[0]
        audit_rules = {
            "pipeline": settings.pipeline,
            "status": "success",
            "rows_read": dry.rows_read,
            "rows_valid": dry.rows_valid,
            "rows_rejected": dry.rows_rejected,
            "rows_loaded": dry.rows_valid,
        }
        if any(audit.get(key) != value for key, value in audit_rules.items()):
            problems.append("audit_counts_or_status_mismatch")

    return {
        "status": "passed" if not problems else "failed",
        "source_rows_read": dry.rows_read,
        "source_rows_valid": dry.rows_valid,
        "source_rows_rejected": dry.rows_rejected,
        "target_rows_observed": len(target_rows),
        "staging_rows_observed": len(stage_rows),
        "audit_rows_observed": len(audit_rows),
        "commit_rows_observed": len(ledger_rows),
        "missing_source_rows": missing_count,
        "unexpected_target_rows": unexpected_count,
        "target_physical_sha256": row_fingerprint(target_rows),
        "mismatch_types": problems,
        "database_writes_executed_by_verifier": False,
        "production_approved": False,
    }


def reconcile_shadow(manifest_path: Path, staging_report: Path,
                     confirmed_schema: str, url_text: str) -> dict[str, Any]:
    """Fail closed and verify a committed disposable MySQL shadow batch."""
    verify(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    staging = json.loads(staging_report.read_text(encoding="utf-8"))
    if not isinstance(staging, dict) or check_staging(staging):
        raise ValueError("A passing MySQL 8 staging report is required")
    url = check_shadow_destination(url_text, confirmed_schema)
    preflight = preflight_shadow(url, manifest["batch_id"], manifest["shadow_target"])
    if not preflight["replay"]:
        raise ValueError("No durable shadow commit exists; execute the approved pilot first")
    cfg, base_dir = load_config(manifest["shadow_yaml"])
    model = resolve_model(cfg.model)
    pipe = Pipeline(cfg, base_dir)
    dry = pipe.run(dry_run=True, batch_id=manifest["batch_id"])
    if not dry.ok or dry.rows_valid <= 0:
        raise ValueError("Source validation failed or no valid records were available")
    checks = cfg.checks.model_copy(deep=True)
    if cfg.target is not None and cfg.target.mode in {"upsert", "scd2"} and not checks.unique:
        checks.unique = cfg.target.key[:]
    core = ValidationEngine(model, cfg.cleaning, checks, base_dir).run(
        pipe._source().read(manifest["batch_id"])
    )
    digest_input = {
        "config": cfg.model_dump(mode="json"),
        "model_schema": model.model_json_schema(),
        "source_digest": core.source_digest,
    }
    second_hash = sha256(json.dumps(
        digest_input, sort_keys=True, default=str, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if (core.source_error or core.dataset_issues or core.rows_valid != dry.rows_valid or
            core.rows_read != dry.rows_read or core.rows_rejected != dry.rows_rejected or
            second_hash != dry.request_hash):
        raise ValueError("Source changed or validation did not reproduce the dry run")
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    try:
        results = compare_shadow(engine, cfg, model, manifest["batch_id"], core.valid, dry)
    finally:
        engine.dispose()
    # Verify source and both configs were unchanged during the read.
    verify(manifest_path)
    return {
        **results,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "toolkit_version": "0.1.2",
        "phase": "shadow_row_reconciliation",
        "schema": confirmed_schema,
        "shadow_target": manifest["shadow_target"],
        "batch_id": manifest["batch_id"],
        "manifest_sha256": sha256_file(manifest_path),
        "staging_report_sha256": sha256_file(staging_report),
        "note": "Read-only reconciliation on disposable MySQL 8; not production authorization",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--staging-report", type=Path, required=True)
    parser.add_argument("--confirm-shadow-schema", required=True)
    parser.add_argument("--report", type=Path, default=Path("shadow-reconciliation.json"))
    args = parser.parse_args()
    try:
        result = reconcile_shadow(args.manifest.resolve(), args.staging_report.resolve(),
                                  args.confirm_shadow_schema, os.environ.get(SHADOW_ENV, ""))
        exit_code = 0 if result["status"] == "passed" else 1
    except Exception as exc:
        # No password, URL or raw source values should be returned in evidence.
        result = {"status": "blocked", "error_type": type(exc).__name__,
                  "database_writes_executed_by_verifier": False, "production_approved": False}
        exit_code = 2
        print(f"BLOCKED: {result['error_type']}; see isolated staging prerequisites", file=sys.stderr)
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Reconciliation status: {result['status']}; report: {report_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
