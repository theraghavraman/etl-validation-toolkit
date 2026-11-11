#!/usr/bin/env python3
"""CI-only MySQL 8 shadow smoke: separate disposable database and stable retry.

Requires an explicitly acknowledged LOCAL disposable MySQL container with the
root password used only for initial test-schema creation. NOT a production tool.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from prepare_shadow_pilot import plan
from run_shadow_pilot import run_pilot
from verify_shadow_reconciliation import reconcile_shadow
from verify_mysql8_staging import is_mysql8

SHADOW_DB = "etlguard_shadow_ci"
SHADOW_PASSWORD = "etlguard_shadow_local_only"


def require_disposable_ci(root_url: str, consent: str):
    from sqlalchemy.engine import make_url

    if consent != "DISPOSABLE_MYSQL_ONLY":
        raise ValueError("Explicit disposable CI acknowledgment required")
    if not root_url:
        raise ValueError("ETLGUARD_CI_MYSQL_ROOT_URL is required")
    url = make_url(root_url)
    if (url.drivername != "mysql+pymysql" or url.username != "root" or
            url.host not in {"127.0.0.1", "localhost"} or url.database != "etlguard_ci"):
        raise ValueError("CI bootstrap restricted to local root@etlguard_ci disposable database")
    return url


def bootstrap(url):
    from sqlalchemy import create_engine, text

    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT VERSION()")).scalar_one()
            existing = conn.execute(text(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME = :db"
            ), {"db": SHADOW_DB}).first()
            if not is_mysql8(version) or existing:
                raise ValueError("CI bootstrap requires MySQL 8 and an UNUSED shadow database")
            conn.commit()
            conn.execute(text(
                "CREATE DATABASE `etlguard_shadow_ci` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            ))
            conn.commit()
            # Password is a fixed *public* disposable-CI-only fixture, not external input.
            conn.execute(text(
                "CREATE USER 'etlguard_shadow'@'%' "
                "IDENTIFIED BY 'etlguard_shadow_local_only'"
            ))
            conn.commit()
            conn.execute(text(
                "GRANT ALL PRIVILEGES ON `etlguard_shadow_ci`.* TO 'etlguard_shadow'@'%'"
            ))
            conn.commit()
        return str(version)
    finally:
        engine.dispose()


def smoke(project: Path, root_url: str, consent: str, staging_report: Path) -> dict:
    from assess_promotion import check_staging

    report = json.loads(staging_report.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or check_staging(report):
        raise ValueError("CI shadow smoke requires a passing MySQL 8 staging report")
    url = require_disposable_ci(root_url, consent)
    version = bootstrap(url)
    shadow_url = url.set(username="etlguard_shadow", password=SHADOW_PASSWORD,
                         database=SHADOW_DB)
    os.environ["ETLGUARD_SHADOW_MYSQL_URL"] = shadow_url.render_as_string(hide_password=False)
    with TemporaryDirectory() as temporary:
        work = Path(temporary)
        plan(project / "src/etlguard/examples/pipelines/customers_clean.yaml", work / "shadow", None, False)
        manifest = work / "shadow/shadow-manifest.json"
        first = run_pilot(manifest, execute=True, confirmed_schema=SHADOW_DB,
                          staging_report=staging_report)
        first_reconciliation = reconcile_shadow(
            manifest, staging_report, SHADOW_DB,
            shadow_url.render_as_string(hide_password=False),
        )
        retry = run_pilot(manifest, execute=True, confirmed_schema=SHADOW_DB,
                          staging_report=staging_report)
        retry_reconciliation = reconcile_shadow(
            manifest, staging_report, SHADOW_DB,
            shadow_url.render_as_string(hide_password=False),
        )
        if (first_reconciliation["status"] != "passed" or
                retry_reconciliation["status"] != "passed" or
                first_reconciliation["target_physical_sha256"] !=
                retry_reconciliation["target_physical_sha256"]):
            raise AssertionError("Independent row-level shadow reconciliation or retry fingerprint failed")
        if (first["status"] != "shadow_committed_not_production" or
                first["replayed"] or retry["replayed"] is not True or
                first["rows_loaded"] < 1 or first["rows_loaded"] != retry["rows_loaded"] or
                first["request_hash"] != retry["request_hash"]):
            raise AssertionError("Shadow retry did not prove stable idempotence")
        return {"status": "passed_disposable_shadow_only", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "mysql_version": version, "shadow_schema": SHADOW_DB,
                "batch_id": first["batch_id"], "rows_loaded": first["rows_loaded"],
                "identical_retry_replayed": retry["replayed"],
                "source_and_target_rows_equal": True,
                "audit_and_ledger_reconciled": True,
                "target_unchanged_after_retry": True,
                "target_physical_sha256": retry_reconciliation["target_physical_sha256"],
                "reconciliation_before_retry": first_reconciliation,
                "reconciliation_after_retry": retry_reconciliation,
                "production_approved": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--report", type=Path, default=Path("shadow-smoke-report.json"))
    parser.add_argument("--staging-report", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = smoke(args.project.resolve(), os.environ.get("ETLGUARD_CI_MYSQL_ROOT_URL", ""),
                       os.environ.get("ETLGUARD_CI_ALLOW_BOOTSTRAP", ""),
                       args.staging_report.resolve())
        code = 0
    except Exception as exc:
        result = {"status": "blocked", "error_type": type(exc).__name__,
                  "production_approved": False}
        print(f"CI shadow smoke blocked: {type(exc).__name__}", file=sys.stderr)
        code = 2
    out = args.report.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CI smoke status: {result['status']}; report: {out}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
