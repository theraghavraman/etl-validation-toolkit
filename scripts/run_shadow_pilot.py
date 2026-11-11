#!/usr/bin/env python3
"""Fail-closed shadow pilot launcher using a pinned manifest and disposable schema.

``validate`` is database-free. ``execute`` is only permitted for a locally
reachable, isolated MySQL 8 shadow schema with matching least-privilege grants.
Never use real customer/application/production schemas or credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from prepare_shadow_pilot import SHADOW_ENV, verify
from verify_mysql8_staging import grants_are_disposable_schema_only, is_mysql8, strict_sql_mode_is_enabled

SHADOW_SCHEMA = re.compile(r"^etlguard_shadow_(?:ci|test|pilot)(?:_[a-z0-9]+)*$")


def check_shadow_destination(text: str, confirmed: str) -> Any:
    from sqlalchemy.engine import make_url

    if not text:
        raise ValueError(f"Set {SHADOW_ENV}; it must point to a DISPOSABLE shadow database")
    url = make_url(text)
    if url.drivername != "mysql+pymysql":
        raise ValueError("Shadow database URL must use mysql+pymysql")
    if not url.host or url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Only localhost/SSH-tunneled shadow MySQL is permitted")
    if not url.database or not SHADOW_SCHEMA.fullmatch(url.database):
        raise ValueError("Shadow schema must be etlguard_shadow_ci/test/pilot (optional suffix)")
    if confirmed != url.database:
        raise ValueError("--confirm-shadow-schema must exactly match the URL schema")
    if not url.username or url.username.lower() in {"root", "admin", "administrator"}:
        raise ValueError("Use a dedicated non-root MySQL shadow user")
    return url


def preflight_shadow(url: Any, batch_id: str, shadow_table: str) -> dict[str, Any]:
    from sqlalchemy import create_engine, text

    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT VERSION() AS v, DATABASE() AS db, @@default_storage_engine AS storage, "
                "@@read_only AS read_only, @@SESSION.sql_mode AS sql_mode"
            )).mappings().one()
            tables = list(conn.execute(text(
                "SELECT table_name, engine FROM information_schema.tables "
                "WHERE table_schema = DATABASE()"
            )).mappings())
            grants = list(conn.execute(text("SHOW GRANTS")).scalars())
            if not is_mysql8(str(row["v"])):
                raise ValueError("A genuine MySQL 8 shadow server is required")
            if row["db"] != url.database or str(row["storage"]).lower() != "innodb" or row["read_only"]:
                raise ValueError("Shadow database/engine/read-write preflight did not pass")
            if not strict_sql_mode_is_enabled(str(row["sql_mode"])):
                raise ValueError("MySQL STRICT SQL mode required for shadow pilot")
            if not grants_are_disposable_schema_only(grants, url.database):
                raise ValueError("Shadow account has broad or inherited grants outside its disposable schema")
            allowed = {"etl_run_audit", "etl_batch_commits", shadow_table, f"stg_{shadow_table}"}
            if any(t["table_name"] not in allowed for t in tables):
                raise ValueError("Shadow schema has unrelated tables; use a fresh isolated schema")
            if any(str(t["engine"] or "").lower() != "innodb" for t in tables):
                raise ValueError("All existing shadow tables must use InnoDB")
            names = {str(t["table_name"]) for t in tables}
            committed = None
            if "etl_batch_commits" in names:
                committed = conn.execute(text(
                    "SELECT batch_id, target_table FROM etl_batch_commits"
                )).mappings().all()
                if any(item["batch_id"] != batch_id or item["target_table"] != shadow_table
                       for item in committed):
                    raise ValueError("Shadow ledger contains an unrelated batch; use a fresh isolated schema")
            if shadow_table in names and not committed:
                raise ValueError("Shadow target pre-exists without a matching committed batch; refuse write")
            if committed and shadow_table not in names:
                raise ValueError("Ledger indicates a committed batch but target table is absent")
            if committed:
                if "etl_run_audit" not in names:
                    raise ValueError("Committed shadow batch is missing its success audit table")
                audit = conn.execute(text(
                    "SELECT status FROM etl_run_audit WHERE batch_id = :batch"
                ), {"batch": batch_id}).scalar_one_or_none()
                if audit != "success":
                    raise ValueError("Committed shadow batch is missing its success audit")
            if f"stg_{shadow_table}" in names:
                staged = conn.execute(text(
                    f"SELECT COUNT(*) FROM `stg_{shadow_table}`"
                )).scalar_one()
                if staged:
                    raise ValueError("Shadow staging table contains leftover rows; investigate before retrying")
            return {"server_version": str(row["v"]), "database": str(row["db"]),
                    "target": shadow_table, "replay": bool(committed),
                    "audit_or_ledger_tables": bool({"etl_run_audit", "etl_batch_commits"} & names)}
    finally:
        engine.dispose()


def run_pilot(manifest: Path, execute: bool, confirmed_schema: str | None = None,
              staging_report: Path | None = None) -> dict[str, Any]:
    from etlguard.pipeline import Pipeline

    verify(manifest)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    yaml_path = Path(record["shadow_yaml"])
    # Validate before connecting to the DB. Failures mean no target writes.
    dry = Pipeline.from_yaml(yaml_path).run(dry_run=True)
    if not dry.ok:
        raise ValueError(f"Shadow dry validation failed: {dry.failures}")
    if not execute:
        return {"status": "validated_only_no_database_writes", "batch_id": record["batch_id"],
                "rows_read": dry.rows_read, "rows_valid": dry.rows_valid,
                "shadow_target": record["shadow_target"]}
    if not confirmed_schema:
        raise ValueError("--confirm-shadow-schema required before any write")
    if staging_report is None:
        raise ValueError("A real passing --staging-report is mandatory before a shadow write")
    from assess_promotion import check_staging
    stage_data = json.loads(staging_report.read_text(encoding="utf-8"))
    if not isinstance(stage_data, dict) or check_staging(stage_data):
        raise ValueError("Staging gate is not fully passed; no shadow database write permitted")
    url = check_shadow_destination(os.environ.get(SHADOW_ENV, ""), confirmed_schema)
    preflight = preflight_shadow(url, record["batch_id"], record["shadow_target"])
    # Re-check immutable inputs immediately before the one authorized shadow write.
    verify(manifest)
    result = Pipeline.from_yaml(yaml_path).run(batch_id=record["batch_id"])
    if not result.ok or not result.durable_commit:
        raise ValueError(f"Shadow run failed (no success asserted): {result.failures}")
    # Verify canonical source snapshot once more after reading it.
    verify(manifest)
    return {"status": "shadow_committed_not_production", "batch_id": record["batch_id"],
            "replayed": result.replayed, "rows_read": result.rows_read,
            "rows_loaded": result.rows_loaded, "rows_inserted": result.rows_inserted,
            "rows_updated": result.rows_updated, "request_hash": result.request_hash,
            "shadow_preflight": preflight, "summary_json": result.summary_json}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--validate", action="store_true", help="Safe local data validation only")
    choice.add_argument("--execute", action="store_true", help="Perform one explicitly authorized SHADOW-only load")
    parser.add_argument("--confirm-shadow-schema", help="Exact disposable schema, required for --execute")
    parser.add_argument("--staging-report", type=Path, help="Passing real MySQL 8 JSON gate; required for --execute")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report: dict[str, Any]
    try:
        report = run_pilot(args.manifest.resolve(), args.execute, args.confirm_shadow_schema,
                           args.staging_report.resolve() if args.staging_report else None)
        code = 0
    except Exception as exc:
        # Avoid accidentally printing DB exception strings/URLs which can contain secrets.
        report = {"status": "blocked", "error_type": type(exc).__name__,
                  "message": "Shadow pilot blocked; see operator logs and verify the isolated database."}
        print(f"BLOCKED: {report['error_type']}: {report['message']}", file=sys.stderr)
        code = 2
    if args.report:
        destination = args.report.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Report: {destination}")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
