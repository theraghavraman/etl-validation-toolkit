#!/usr/bin/env python3
"""Read-only evidence assessor: never promotes or modifies a database.

Passing this assessor means *ready for human release review*, NOT approved to
run production. All operational evidence must be independently reviewed.
"""
from __future__ import annotations

import argparse
from etlguard import __version__

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Any

REQUIRED_AUTOMATED = ("mysql8_integration", "complete_suite", "ruff", "mypy")
REQUIRED_OPERATIONAL = (
    "immutable_source", "backup_restore_rehearsal", "shadow_row_reconciliation",
    "same_batch_retry", "changed_input_rejected", "commit_outcome_recovery",
    "scd2_concurrency", "rollback_rehearsal", "monitoring_observation",
    "supervised_real_target_pilot",
)
REQUIRED_APPROVERS = ("data_owner", "database_owner", "operations_owner")


def digest_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_staging(report: dict[str, Any], *, now: datetime | None = None) -> list[str]:
    reasons: list[str] = []
    if report.get("toolkit_version") != __version__:
        reasons.append(f"Staging report version is not v{__version__}")
    if (report.get("status") != "passed" or not report.get("mysql8_server_verified")
            or not report.get("mysql8_integration_executed")):
        reasons.append("A completed, passing real MySQL 8 staging gate is required")
    preflight = report.get("preflight") or {}
    version = str(preflight.get("server_version", ""))
    if not re.match(r"^8\.\d+\.", version) or "mariadb" in version.lower():
        reasons.append("Staging server was not verified as Oracle MySQL 8")
    if str(preflight.get("default_storage_engine", "")).lower() != "innodb":
        reasons.append("Staging server's default engine was not InnoDB")
    if preflight.get("unexpected_tables"):
        reasons.append("Staging schema contained unexpected tables")
    if preflight.get("read_only") is not False:
        reasons.append("Staging preflight did not verify read/write test schema")
    if preflight.get("strict_sql_mode") is not True:
        reasons.append("Staging preflight did not verify STRICT SQL mode")
    if preflight.get("schema_only_privileges") is not True:
        reasons.append("Staging preflight did not verify least-privilege test credentials")
    if preflight.get("non_innodb_tables"):
        reasons.append("Staging preflight found non-transactional tables")
    timestamp = report.get("timestamp_utc")
    try:
        generated = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError("Missing timezone")
        current = now or datetime.now(timezone.utc)
        if generated > current + timedelta(minutes=5) or current - generated > timedelta(days=30):
            reasons.append("Staging report is outside the 30-day validity window")
    except ValueError:
        reasons.append("Staging report does not contain a valid timezone-aware timestamp")
    checks = report.get("checks") or []
    if not isinstance(checks, list):
        checks = []
    by_name = {item.get("name"): item for item in checks if isinstance(item, dict)}
    for key in REQUIRED_AUTOMATED:
        entry = by_name.get(key)
        if not entry or entry.get("status") != "passed" or entry.get("exit_code") != 0 or entry.get("skipped") != 0:
            reasons.append(f"Automated gate '{key}' did not pass with zero skips")
    return reasons


def check_operational(evidence: dict[str, Any], evidence_file: Path) -> tuple[list[str], dict[str, str]]:
    reasons: list[str] = []
    hashes: dict[str, str] = {}
    if evidence.get("toolkit_version") != __version__:
        reasons.append(f"Pilot evidence version is not v{__version__}")
    for section, keys in [("operational", REQUIRED_OPERATIONAL), ("approvals", REQUIRED_APPROVERS)]:
        entries = evidence.get(section) or {}
        if not isinstance(entries, dict):
            entries = {}
        for key in keys:
            record = entries.get(key) or {}
            if not isinstance(record, dict) or record.get("verified") is not True:
                reasons.append(f"Missing verified {section}: {key}")
                continue
            path_text = record.get("evidence_file")
            if not isinstance(path_text, str) or not path_text.strip():
                reasons.append(f"Missing artifact for {section}.{key}")
                continue
            file_path = (evidence_file.parent / path_text).resolve()
            # Do not allow manifest relative traversal to unintentionally read private files.
            if not file_path.is_relative_to(evidence_file.parent.resolve()):
                reasons.append(f"Evidence path outside directory for {section}.{key}")
                continue
            if not file_path.is_file() or file_path.stat().st_size == 0:
                reasons.append(f"Missing/empty evidence file for {section}.{key}")
                continue
            hashes[f"{section}.{key}"] = digest_file(file_path)
    return reasons, hashes


def assess(staging: Path, evidence: Path) -> dict[str, Any]:
    staging_data = json.loads(staging.read_text(encoding="utf-8"))
    evidence_data = json.loads(evidence.read_text(encoding="utf-8"))
    if not isinstance(staging_data, dict) or not isinstance(evidence_data, dict):
        raise ValueError("Both JSON files must contain objects")
    staging_reasons = check_staging(staging_data)
    operational_reasons, files = check_operational(evidence_data, evidence)
    reasons = staging_reasons + operational_reasons
    return {
        "assessed_utc": datetime.now(timezone.utc).isoformat(),
        "toolkit_version": __version__,
        "decision": "blocked" if reasons else "review_ready_not_authorized",
        "production_approved": False,
        "database_writes_executed": False,
        "reasons": reasons,
        "staging_report_sha256": digest_file(staging),
        "evidence_manifest_sha256": digest_file(evidence),
        "evidence_artifact_sha256": files,
        "disclaimer": "Artifact presence and user assertions are not cryptographic signatures. Human approval is still required.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("promotion-review.json"))
    args = parser.parse_args()
    try:
        result = assess(args.staging.resolve(), args.evidence.resolve())
        out = args.report.resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Decision: {result['decision']}; unmet gates: {len(result['reasons'])}; report: {out}")
        return 0 if result["decision"] == "review_ready_not_authorized" else 2
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
