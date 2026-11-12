#!/usr/bin/env python3
"""Prepare and verify a shadow-only pilot manifest. Never connects to a database.

Intentionally does not execute ``etlguard run``. Operators must verify the
isolated database and table before allowing any write, using least privilege.
"""
from __future__ import annotations

import argparse
from etlguard import __version__

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shlex
import sys
from uuid import UUID, uuid4
from typing import Any

import yaml

from etlguard.config import IDENTIFIER, load_config

SHADOW_ENV = "ETLGUARD_SHADOW_MYSQL_URL"


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def shadow_table_name(original: str, specified: str | None = None) -> str:
    name = specified or ("pilot_" + original[:42])
    if not IDENTIFIER.fullmatch(name) or len(name) > 48 or not name.startswith("pilot_"):
        raise ValueError("Shadow target must be a SQL identifier <=48 characters beginning pilot_")
    if name == original:
        raise ValueError("Shadow target must differ from the source configuration target")
    if name.startswith("pilot_stg_"):
        raise ValueError("Do not use staging-table-like names for shadow target")
    return name


def plan(config: Path, outdir: Path, requested_table: str | None, allow_shadow_replace: bool) -> dict[str, Any]:
    settings, base_dir = load_config(config)
    if settings.target is None:
        raise ValueError("A target is required to plan a database shadow pilot")
    if settings.source.type not in {"csv", "excel"}:
        raise ValueError("Shadow pilot requires an immutable local CSV/Excel snapshot, not an API feed")
    source_path = (base_dir / (settings.source.path or "")).resolve()
    if not source_path.is_file():
        raise ValueError(f"Source snapshot does not exist: {source_path}")
    if source_path.stat().st_size == 0:
        raise ValueError("Source snapshot is empty; review it instead of planning a database load")
    if settings.target.mode == "truncate_insert" and not allow_shadow_replace:
        raise ValueError("Snapshot replacement requires explicit --allow-shadow-replace, on an ISOLATED database")
    table_name = shadow_table_name(settings.target.table, requested_table)
    outdir = outdir.resolve()
    pilot_yaml = outdir / "shadow-pipeline.yaml"
    manifest_path = outdir / "shadow-manifest.json"
    if pilot_yaml.exists() or manifest_path.exists():
        raise FileExistsError("Pilot already prepared: refusing to overwrite its stable batch ID or snapshot metadata")
    snapshot_hash = sha256_file(source_path)
    config_hash = sha256_file(config.resolve())
    pilot = settings.model_dump(mode="json")
    pilot["pipeline"] = (settings.pipeline[:115] + "_shadow")
    pilot["source"]["path"] = str(source_path)
    pilot["target"]["table"] = table_name
    pilot["database_url_env"] = SHADOW_ENV
    pilot["output"]["directory"] = str(outdir / "reports")
    # Never carry dangerous live replacement overrides into a new pilot.
    pilot["policy"]["allow_partial_replace"] = False
    pilot["policy"]["allow_empty_replace"] = False
    pilot["target"]["min_replace_ratio"] = pilot["target"].get("min_replace_ratio") or (
        0.9 if pilot["target"]["mode"] == "truncate_insert" else None
    )
    outdir.mkdir(parents=True, exist_ok=True)
    pilot_yaml.write_text(yaml.safe_dump(pilot, sort_keys=False), encoding="utf-8")
    batch_id = str(uuid4())
    manifest: dict[str, Any] = {
        "format_version": 1,
        "toolkit_version": __version__,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "prepared_not_executed",
        "batch_id": batch_id,
        "source_path": str(source_path),
        "source_bytes": source_path.stat().st_size,
        "source_sha256": snapshot_hash,
        "original_yaml": str(config.resolve()),
        "original_yaml_sha256": config_hash,
        "shadow_yaml": str(pilot_yaml),
        "shadow_yaml_sha256": sha256_file(pilot_yaml),
        "original_target": settings.target.table,
        "shadow_target": table_name,
        "target_mode": settings.target.mode,
        "database_url_env": SHADOW_ENV,
        "warning": "No DB connection was made. Set a dedicated SHADOW MySQL URL; never a live-target URL.",
        "commands": {
            "verify_snapshot": f"python scripts/prepare_shadow_pilot.py verify --manifest {shlex.quote(str(manifest_path))}",
            "validate_only": f"etlguard validate {shlex.quote(str(pilot_yaml))}",
            "shadow_load_after_approval": (
                f"python scripts/run_shadow_pilot.py --manifest {shlex.quote(str(manifest_path))} "
                f"--execute --confirm-shadow-schema etlguard_shadow_ci "
                f"--staging-report staging-gate-report.json"
            ),
            "check_commit": f"etlguard batch-status {batch_id} --database-url-env {SHADOW_ENV}",
        },
    }
    # Restrict reading of pipeline locations and operational metadata where possible.
    fd = manifest_path.open("x", encoding="utf-8")
    try:
        json.dump(manifest, fd, indent=2, sort_keys=True)
        fd.write("\n")
    finally:
        fd.close()
    manifest_path.chmod(0o600)
    return manifest


def verify(manifest_file: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    required = {"source_path", "source_sha256", "source_bytes", "original_yaml", "original_yaml_sha256",
                "shadow_yaml", "shadow_yaml_sha256", "batch_id", "shadow_target", "database_url_env"}
    if not required.issubset(manifest) or manifest.get("format_version") != 1:
        raise ValueError("Invalid/incomplete shadow pilot manifest")
    if str(UUID(manifest["batch_id"])) != manifest["batch_id"]:
        raise ValueError("Batch ID is not a canonical UUID")
    if manifest["database_url_env"] != SHADOW_ENV:
        raise ValueError("Shadow database environment variable was altered")
    shadow_table_name(manifest["original_target"], manifest["shadow_target"])
    checked = []
    for label, pathname, recorded in [
        ("source", "source_path", "source_sha256"),
        ("original_yaml", "original_yaml", "original_yaml_sha256"),
        ("shadow_yaml", "shadow_yaml", "shadow_yaml_sha256"),
    ]:
        path = Path(manifest[pathname])
        if not path.is_file() or sha256_file(path) != manifest[recorded]:
            raise ValueError(f"{label} changed or disappeared; do NOT reuse this batch ID")
        checked.append(label)
    if Path(manifest["source_path"]).stat().st_size != manifest["source_bytes"]:
        raise ValueError("Source length changed; do NOT reuse this batch ID")
    settings, _ = load_config(manifest["shadow_yaml"])
    if (settings.target is None or settings.target.table != manifest["shadow_target"] or
            settings.database_url_env != SHADOW_ENV):
        raise ValueError("Shadow configuration does not match the manifest")
    return {"status": "snapshot_verified_not_executed", "checks": checked,
            "batch_id": manifest["batch_id"], "shadow_target": manifest["shadow_target"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("plan", help="Create a non-overwriting shadow configuration and stable manifest")
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--shadow-table")
    p.add_argument("--allow-shadow-replace", action="store_true")
    v = commands.add_parser("verify", help="Confirm source/config checksums before any manual execution")
    v.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = (plan(args.config, args.output, args.shadow_table, args.allow_shadow_replace)
                  if args.command == "plan" else verify(args.manifest))
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
