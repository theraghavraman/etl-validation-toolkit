#!/usr/bin/env python3
"""Fail-closed MySQL 8 staging gate for ETL Guard v0.1.2.

This runner creates/drops test tables indirectly through pytest integration tests.
It is intentionally restricted to disposable schemas that are explicitly confirmed.
It never connects to the production target database.
"""
from __future__ import annotations

import argparse
from etlguard import __version__

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, unquote
from typing import Any

SCHEMA_PATTERN = re.compile(r"^etlguard_(?:ci|test|staging_ci)(?:_[a-z0-9]+)*$")
ALLOWED_TABLES = {"etl_run_audit", "etl_batch_commits"}


def validate_destination(url_text: str, confirmed_schema: str, allow_remote: bool = False) -> Any:
    """Reject production-looking destinations before opening a connection."""
    from sqlalchemy.engine import make_url

    if not url_text:
        raise ValueError("ETLGUARD_TEST_MYSQL_URL is not set")
    url = make_url(url_text)
    if url.drivername != "mysql+pymysql":
        raise ValueError("Test URL must use mysql+pymysql:// (not SQLite or MariaDB)")
    if not url.database or not SCHEMA_PATTERN.fullmatch(url.database):
        raise ValueError("Test database must be named etlguard_ci, etlguard_test, or etlguard_staging_ci (optional suffix)")
    if confirmed_schema != url.database:
        raise ValueError("--confirm-disposable-schema must EXACTLY match the URL database")
    if (url.username or "").lower() in {"root", "admin", "administrator", ""}:
        raise ValueError("A non-root, dedicated disposable-schema MySQL user is required")
    if url.host not in {"127.0.0.1", "localhost", "::1"} and not allow_remote:
        raise ValueError("Remote MySQL hosts require explicit --allow-remote")
    if not url.host:
        raise ValueError("URL must specify the MySQL hostname")
    return url


def is_mysql8(version: str) -> bool:
    return bool(re.match(r"^8\.\d+\.", version)) and "mariadb" not in version.lower()


def disallowed_tables(names: list[str]) -> list[str]:
    return sorted(name for name in names if not (
        name in ALLOWED_TABLES or name.startswith("it_") or name.startswith("stg_it_")
    ))


def grants_are_disposable_schema_only(grants: list[str], schema: str) -> bool:
    """Allow only USAGE globally and direct grants on the disposable schema.

    Disallow role grants rather than trying to resolve inherited global privileges.
    """
    schema_sql = re.escape(schema)
    limited = re.compile(r"\bON\s+`?" + schema_sql + r"`?\.\*\s+TO\b", re.I)
    usage = re.compile(r"^GRANT\s+USAGE\s+ON\s+\*\.\*\s+TO\b", re.I)
    # SHOW GRANTS escapes literal underscores in database identifiers.
    return bool(grants) and all(
        usage.search(grant) or limited.search(grant.replace("\\_", "_"))
        for grant in grants
    )


def strict_sql_mode_is_enabled(sql_mode: str) -> bool:
    """Silent value coercion and truncation are unsafe for financial/data loads."""
    modes = {item.strip().upper() for item in sql_mode.split(",")}
    return bool({"STRICT_TRANS_TABLES", "STRICT_ALL_TABLES"} & modes)


def redact_secrets(message: str, url_text: str) -> str:
    """Do not leak database passwords into the gate status or persisted logs."""
    from sqlalchemy.engine import make_url

    secrets = [url_text]
    try:
        password = make_url(url_text).password or ""
        secrets.extend([password, quote(password, safe=""), unquote(password)])
    except Exception:
        pass
    for secret in sorted(set(secrets), key=len, reverse=True):
        if len(secret) >= 4:
            message = message.replace(secret, "<redacted>")
    return message


def preflight(url: Any) -> dict[str, Any]:
    from sqlalchemy import create_engine, text

    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT VERSION() AS version, DATABASE() AS db_name, "
                "@@default_storage_engine AS storage_engine, @@read_only AS read_only, "
                "@@SESSION.sql_mode AS sql_mode"
            )).mappings().one()
            tables = list(conn.execute(text(
                "SELECT table_name AS table_name, engine AS engine, table_type AS table_type FROM information_schema.tables "
                "WHERE table_schema = DATABASE()"
            )).mappings())
            grants = list(conn.execute(text("SHOW GRANTS")).scalars())
        names = [str(row["table_name"]) for row in tables]
        bad_engines = [str(t["table_name"]) for t in tables
                       if str(t["engine"] or "").lower() != "innodb" or
                       str(t["table_type"]).upper() != "BASE TABLE"]
        info = {
            "server_version": str(row["version"]),
            "database": str(row["db_name"]),
            "default_storage_engine": str(row["storage_engine"]),
            "read_only": bool(row["read_only"]),
            "strict_sql_mode": strict_sql_mode_is_enabled(str(row["sql_mode"])),
            "schema_only_privileges": grants_are_disposable_schema_only(grants, url.database),
            "existing_table_count": len(names),
            "unexpected_tables": disallowed_tables(names),
            "non_innodb_tables": bad_engines,
        }
        if not is_mysql8(info["server_version"]):
            raise ValueError(f"Expected MySQL 8.x, found {info['server_version']}")
        if info["database"] != url.database:
            raise ValueError("Connected schema does not match the confirmed disposable schema")
        if info["default_storage_engine"].lower() != "innodb":
            raise ValueError("MySQL default storage engine must be InnoDB")
        if info["read_only"]:
            raise ValueError("Staging server is read-only; tests need disposable-schema DDL")
        if not info["strict_sql_mode"]:
            raise ValueError("MySQL strict SQL mode is required to avoid silent coercion/truncation")
        if not info["schema_only_privileges"]:
            raise ValueError("Test identity has non-disposable-schema or inherited global privileges")
        if info["unexpected_tables"]:
            raise ValueError(f"Unexpected tables in test schema: {info['unexpected_tables']}")
        if info["non_innodb_tables"]:
            raise ValueError(f"Existing tables are not InnoDB: {info['non_innodb_tables']}")
        return info
    finally:
        engine.dispose()


def execute_gate(project: Path, environment: dict[str, str], logs: Path) -> list[dict[str, Any]]:
    tests = [
        ("mysql8_integration", [sys.executable, "-m", "pytest", "-q", "-ra", "-m", "integration", "tests/test_mysql_integration.py"]),
        ("complete_suite", [sys.executable, "-m", "pytest", "-q", "-ra", "--cov=etlguard", "--cov-branch", "--cov-fail-under=85"]),
        ("ruff", [sys.executable, "-m", "ruff", "check", "."]),
        ("mypy", [sys.executable, "-m", "mypy", "src/etlguard"]),
    ]
    results: list[dict[str, Any]] = []
    for name, cmd in tests:
        print(f"Running {name}: {' '.join(cmd)}", flush=True)
        try:
            completed = subprocess.run(cmd, cwd=project, env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, errors="replace", timeout=900, check=False)
            output = completed.stdout
            code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            output = str(exc)
            code = 124
        # No database URL or credentials are printed in normal gate logs.
        output = redact_secrets(output, environment.get("ETLGUARD_TEST_MYSQL_URL", ""))
        (logs / f"{name}.log").write_text(output, encoding="utf-8")
        skipped = sum(int(num) for num in re.findall(r"(?:^|\s)(\d+) skipped\b", output))
        result = {
            "name": name, "exit_code": code, "skipped": skipped,
            "status": "passed" if code == 0 and skipped == 0 else "failed",
            "log": str(logs / f"{name}.log"),
        }
        results.append(result)
        print(f"  {result['status']} (exit={code}, skipped={skipped})", flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="Path to extracted v0.1.2 source project")
    parser.add_argument("--confirm-disposable-schema", required=True,
                        help="Exact disposable database name (for example etlguard_ci)")
    parser.add_argument("--allow-remote", action="store_true",
                        help="Permit a named remote test server (never a production DB)")
    parser.add_argument("--execute-tests", action="store_true",
                        help="Run destructive integration tests after the safe preflight")
    parser.add_argument("--report", type=Path, default=Path("staging-gate-report.json"))
    args = parser.parse_args()

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "toolkit_version": __version__,
        "gate": "mysql8_staging",
        "status": "blocked",
        "mysql8_server_verified": False,
        "mysql8_integration_executed": False,
        "production_pilot_executed": False,
        "preflight": None,
        "checks": [],
        "note": "A staging pass is NOT authorization for production deployment.",
    }
    exit_code = 2
    url_text = os.environ.get("ETLGUARD_TEST_MYSQL_URL", "")
    try:
        project = args.project.expanduser().resolve()
        if not (project / "tests/test_mysql_integration.py").is_file():
            raise ValueError("--project must point to extracted ETL Guard source with tests/")
        url = validate_destination(url_text, args.confirm_disposable_schema, args.allow_remote)
        print(f"Checking disposable MySQL destination: {url.host}:{url.port or 3306}/{url.database}")
        report["preflight"] = preflight(url)
        report["mysql8_server_verified"] = True
        if not args.execute_tests:
            report["status"] = "preflight_passed_only"
            report["note"] = "Re-run with --execute-tests for the MySQL, quality, lint, and type gates."
            exit_code = 0
        else:
            env = os.environ.copy()
            env["ETLGUARD_DATABASE_URL"] = url_text
            # An editable package installation is preferred; PYTHONPATH also permits source tests.
            env["PYTHONPATH"] = str(project / "src") + os.pathsep + env.get("PYTHONPATH", "")
            logs = args.report.expanduser().resolve().parent / "mysql8-gate-logs"
            logs.mkdir(parents=True, exist_ok=True)
            checks = execute_gate(project, env, logs)
            report["mysql8_integration_executed"] = True
            report["checks"] = checks
            report["status"] = "passed" if all(c["status"] == "passed" for c in checks) else "failed"
            report["note"] = ("MySQL8 + static/functional staging gates passed; a separate "
                              "approved controlled pilot is still required."
                              if report["status"] == "passed" else
                              "One or more gates failed or were skipped. Do not deploy.")
            exit_code = 0 if report["status"] == "passed" else 1
    except Exception as exc:
        reason = redact_secrets(str(exc), url_text)
        print(f"BLOCKED: {reason}", file=sys.stderr)
        report["error"] = reason
    finally:
        out = args.report.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Gate status: {report['status']}; report: {out}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
