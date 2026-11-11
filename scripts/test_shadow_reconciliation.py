"""Offline tests for independent pilot reconciliation; SQLite is NOT MySQL certification."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from unittest.mock import patch

import yaml
from sqlalchemy import MetaData, Table, create_engine, select

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_shadow_pilot import plan, verify
from verify_shadow_reconciliation import (
    canonical, compare_shadow, reconcile_shadow, row_fingerprint,
)
from etlguard.config import load_config
from etlguard.core import ValidationEngine
from etlguard.loaders.mysql import MysqlLoader
from etlguard.pipeline import Pipeline, resolve_model


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def setup_loaded(self, mode="append"):
        source = self.root / "source.csv"
        source.write_text(
            "customer_id,full_name,email,signup_date,status,credit_limit\n"
            "C001,Alice Doe,alice@example.com,2026-01-01,active,100.00\n"
            "C002,Bob Doe,bob@example.com,2026-01-02,active,200.00\n",
            encoding="utf-8",
        )
        config = self.root / "config.yaml"
        config.write_text(yaml.safe_dump({
            "pipeline": "integration_qa",
            "source": {"type": "csv", "path": "source.csv"},
            "model": "etlguard.examples.models:Customer",
            "target": {"table": "customers", "mode": mode, "key": ["customer_id"] if mode == "scd2" else []},
        }), encoding="utf-8")
        out = self.root / "pilot"
        manifest = plan(config, out, None, False)
        verify(out / "shadow-manifest.json")
        cfg, base = load_config(out / "shadow-pipeline.yaml")
        model = resolve_model(cfg.model)
        pipe = Pipeline(cfg, base)
        dry = pipe.run(dry_run=True, batch_id=manifest["batch_id"])
        self.assertTrue(dry.ok)
        checks = cfg.checks.model_copy(deep=True)
        if cfg.target.mode in {"scd2", "upsert"} and not checks.unique:
            checks.unique = cfg.target.key[:]
        core = ValidationEngine(model, cfg.cleaning, checks, base).run(
            pipe._source().read(manifest["batch_id"])
        )
        engine = create_engine(f"sqlite:///{self.root / 'database.sqlite'}")
        self.addCleanup(engine.dispose)
        loader = MysqlLoader(engine=engine)
        loader.load(core.valid, manifest["batch_id"], model, cfg.target,
                    request_hash=dry.request_hash, audit_result=dry)
        return engine, cfg, model, manifest, core, dry, loader

    def compare(self, state):
        engine, cfg, model, manifest, core, dry, _ = state
        return compare_shadow(engine, cfg, model, manifest["batch_id"], core.valid, dry)

    def test_clean_first_load_matches_full_rows_and_audits(self):
        result = self.compare(self.setup_loaded())
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["source_rows_valid"], 2)
        self.assertEqual(result["target_rows_observed"], 2)
        self.assertEqual(result["missing_source_rows"], 0)
        self.assertFalse(result["database_writes_executed_by_verifier"])
        self.assertNotIn("Alice Doe", json.dumps(result))

    def test_stable_retry_preserves_physical_row_fingerprint(self):
        state = self.setup_loaded()
        before = self.compare(state)
        engine, cfg, model, manifest, core, dry, loader = state
        retry = loader.load(core.valid, manifest["batch_id"], model, cfg.target,
                            request_hash=dry.request_hash, audit_result=dry)
        self.assertTrue(retry.replayed)
        after = self.compare(state)
        self.assertEqual(before["target_physical_sha256"], after["target_physical_sha256"])
        self.assertEqual(after["status"], "passed")

    def test_same_count_but_corrupted_customer_is_detected(self):
        state = self.setup_loaded()
        engine, cfg, *_ = state
        target = Table(cfg.target.table, MetaData(), autoload_with=engine)
        with engine.begin() as conn:
            conn.execute(target.update().where(target.c.customer_id == "C001").values(
                full_name="Altered Customer"
            ))
        result = self.compare(state)
        self.assertEqual(result["status"], "failed")
        self.assertIn("source_vs_persisted_row_values_mismatch", result["mismatch_types"])
        self.assertEqual(result["target_rows_observed"], 2)
        self.assertNotIn("Altered Customer", json.dumps(result))

    def test_commit_hash_change_is_detected(self):
        state = self.setup_loaded()
        engine = state[0]
        commits = Table("etl_batch_commits", MetaData(), autoload_with=engine)
        with engine.begin() as conn:
            conn.execute(commits.update().values(request_hash="0" * 64))
        result = self.compare(state)
        self.assertIn("batch_commit_ledger_mismatch", result["mismatch_types"])

    def test_audit_count_change_is_detected(self):
        state = self.setup_loaded()
        engine = state[0]
        audit = Table("etl_run_audit", MetaData(), autoload_with=engine)
        with engine.begin() as conn:
            conn.execute(audit.update().values(rows_valid=400))
        result = self.compare(state)
        self.assertIn("audit_counts_or_status_mismatch", result["mismatch_types"])

    def test_staging_leftover_is_detected(self):
        state = self.setup_loaded()
        engine, cfg, *_ = state
        stage = Table(f"stg_{cfg.target.table}", MetaData(), autoload_with=engine)
        with engine.begin() as conn:
            conn.execute(stage.insert().values(
                __etl_id=str(uuid4()), __etl_batch_id=str(uuid4()),
                __etl_source_row_number=42, __etl_ingested_at=datetime.now(timezone.utc),
                customer_id="C003", full_name="Injected Stage", email="a@example.com",
                signup_date=date(2026, 1, 3), status="active", credit_limit=100,
            ))
        result = self.compare(state)
        self.assertIn("staging_rows_remain", result["mismatch_types"])

    def test_scd2_first_load_has_one_current_per_key(self):
        state = self.setup_loaded("scd2")
        self.assertEqual(self.compare(state)["status"], "passed")
        engine, cfg, *_ = state
        target = Table(cfg.target.table, MetaData(), autoload_with=engine)
        with engine.begin() as conn:
            conn.execute(target.insert().values(
                __etl_id=str(uuid4()), customer_id="C001", full_name="Earlier",
                email="alice@example.com", signup_date=date(2026, 1, 1),
                status="active", credit_limit=Decimal("100.00"),
                effective_from=datetime(2020, 1, 1),
                effective_to=datetime(2021, 1, 1), is_current=False,
            ))
        result = self.compare(state)
        self.assertIn("unexpected_scd2_history_during_first_batch", result["mismatch_types"])

    def test_ledger_anomaly_and_extra_rows_detected(self):
        state = self.setup_loaded()
        engine, cfg, *_ = state
        ledger = Table("etl_batch_commits", MetaData(), autoload_with=engine)
        with engine.begin() as conn:
            existing = conn.execute(select(ledger)).mappings().first()
            conn.execute(ledger.insert().values({
                **dict(existing), "batch_id": str(uuid4()),
            }))
        result = self.compare(state)
        self.assertIn("ledger_batch_count_or_identity_mismatch", result["mismatch_types"])

    def test_fingerprint_independent_of_observation_order(self):
        rows = [{"n": Decimal("100.00"), "id": "1"},
                {"n": Decimal("200.0"), "id": "2"}]
        self.assertEqual(row_fingerprint(rows), row_fingerprint(list(reversed(rows))))
        self.assertEqual(canonical(Decimal("100.00")), canonical(Decimal("100")))

    def test_wrapper_reconciles_using_offline_mocked_mysql_preflight(self):
        state = self.setup_loaded()
        engine = state[0]
        manifest_path = self.root / "pilot/shadow-manifest.json"
        staging = self.root / "stage.json"
        staging.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
        with (patch("verify_shadow_reconciliation.check_staging", return_value=[]),
              patch("verify_shadow_reconciliation.check_shadow_destination", return_value=object()),
              patch("verify_shadow_reconciliation.preflight_shadow", return_value={"replay": True}),
              patch("verify_shadow_reconciliation.create_engine", return_value=engine)):
            result = reconcile_shadow(manifest_path, staging, "etlguard_shadow_ci", "fake")
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(result["target_physical_sha256"])
        self.assertFalse(result["production_approved"])

    def test_changed_source_blocks_wrapper_before_database_access(self):
        self.setup_loaded()
        source = self.root / "source.csv"
        source.write_text(source.read_text(encoding="utf-8") +
                          "C003,Eve,eve@example.com,2026-01-03,active,300.00\n", encoding="utf-8")
        with patch("verify_shadow_reconciliation.create_engine") as database:
            with self.assertRaisesRegex(ValueError, "changed or disappeared"):
                reconcile_shadow(self.root / "pilot/shadow-manifest.json",
                                 self.root / "missing-staging.json", "etlguard_shadow_ci", "")
            database.assert_not_called()

    def test_cli_wrapper_rejects_missing_staging_evidence_before_db_connection(self):
        state = self.setup_loaded()
        *_, manifest, _, _, _ = state
        manifest_path = self.root / "pilot/shadow-manifest.json"
        stage = self.root / "staging.json"
        stage.write_text(json.dumps({"status": "blocked"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "passing MySQL 8 staging report"):
            reconcile_shadow(manifest_path, stage, "etlguard_shadow_ci", "")


if __name__ == "__main__":
    unittest.main()
