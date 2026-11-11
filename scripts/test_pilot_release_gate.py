"""Offline regression tests for shadow snapshot and human promotion review gates."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from assess_promotion import (
    REQUIRED_APPROVERS, REQUIRED_AUTOMATED, REQUIRED_OPERATIONAL,
    assess, check_staging, check_operational,
)
from prepare_shadow_pilot import plan, verify, shadow_table_name
from run_shadow_pilot import check_shadow_destination, run_pilot
from ci_shadow_smoke import require_disposable_ci
from verify_mysql8_staging import grants_are_disposable_schema_only, strict_sql_mode_is_enabled


class PilotGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.csv = self.root / "feed.csv"
        self.csv.write_text("customer_id,full_name,email,signup_date,status,credit_limit\n"
                            "C001,Alice Person,alice@example.com,2026-01-01,active,100\n",
                            encoding="utf-8")
        self.yaml = self.root / "pipeline.yaml"
        self.original = {
            "pipeline": "customers_daily", "source": {"type": "csv", "path": "feed.csv"},
            "model": "etlguard.examples.models:Customer",
            "target": {"table": "customers", "mode": "upsert", "key": ["customer_id"]},
            "output": {"directory": "reports"},
        }
        self.write_config()
        self.out = self.root / "pilot"

    def write_config(self):
        self.yaml.write_text(yaml.safe_dump(self.original), encoding="utf-8")

    def staging_ok(self):
        return {
            "toolkit_version": "0.1.2", "status": "passed", "mysql8_server_verified": True,
            "mysql8_integration_executed": True,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "preflight": {"server_version": "8.4.7", "default_storage_engine": "InnoDB",
                          "unexpected_tables": [], "read_only": False,
                          "strict_sql_mode": True, "schema_only_privileges": True,
                          "non_innodb_tables": []},
            "checks": [{"name": key, "status": "passed", "skipped": 0, "exit_code": 0}
                       for key in REQUIRED_AUTOMATED],
        }

    def test_shadow_plan_never_executes_and_verifies_hashes(self):
        manifest = plan(self.yaml, self.out, None, False)
        self.assertEqual(manifest["status"], "prepared_not_executed")
        self.assertEqual(manifest["database_url_env"], "ETLGUARD_SHADOW_MYSQL_URL")
        self.assertEqual(manifest["shadow_target"], "pilot_customers")
        self.assertEqual(verify(self.out / "shadow-manifest.json")["status"],
                         "snapshot_verified_not_executed")
        settings = yaml.safe_load((self.out / "shadow-pipeline.yaml").read_text())
        self.assertEqual(settings["target"]["table"], "pilot_customers")
        self.assertNotEqual(settings["source"]["path"], "feed.csv")

    def test_does_not_overwrite_persisted_batch_id(self):
        plan(self.yaml, self.out, None, False)
        with self.assertRaises(FileExistsError):
            plan(self.yaml, self.out, None, False)

    def test_changed_source_blocks_same_batch_id(self):
        plan(self.yaml, self.out, None, False)
        self.csv.write_text(self.csv.read_text() + "C002,Bob\n")
        with self.assertRaisesRegex(ValueError, "changed or disappeared"):
            verify(self.out / "shadow-manifest.json")

    def test_changed_yaml_blocks_same_batch_id(self):
        plan(self.yaml, self.out, None, False)
        self.original["pipeline"] = "changed"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "changed or disappeared"):
            verify(self.out / "shadow-manifest.json")

    def test_changed_shadow_yaml_blocks_same_batch_id(self):
        plan(self.yaml, self.out, None, False)
        with (self.out / "shadow-pipeline.yaml").open("a") as f:
            f.write("\n# changed\n")
        with self.assertRaisesRegex(ValueError, "changed or disappeared"):
            verify(self.out / "shadow-manifest.json")

    def test_blocks_api_without_immutable_snapshot(self):
        self.original["source"] = {"type": "api", "url": "https://example.com/api"}
        self.write_config()
        with self.assertRaisesRegex(ValueError, "immutable local"):
            plan(self.yaml, self.out, None, False)

    def test_replace_requires_explicit_shadow_opt_in(self):
        self.original["target"] = {"table": "customers", "mode": "truncate_insert"}
        self.original["policy"] = {"allow_empty_replace": True, "allow_partial_replace": True}
        self.write_config()
        with self.assertRaisesRegex(ValueError, "requires explicit"):
            plan(self.yaml, self.out, None, False)
        plan(self.yaml, self.out, None, True)
        configured = yaml.safe_load((self.out / "shadow-pipeline.yaml").read_text())
        self.assertFalse(configured["policy"]["allow_empty_replace"])
        self.assertFalse(configured["policy"]["allow_partial_replace"])
        self.assertEqual(configured["target"]["min_replace_ratio"], 0.9)

    def test_shadow_table_identifier_guard(self):
        for name in ["customers", "orders", "prod_payments", "pilot_a;DROP TABLE", "pilot_stg_rows"]:
            with self.assertRaises(ValueError, msg=name):
                shadow_table_name("customers", name)
        self.assertEqual(shadow_table_name("customers"), "pilot_customers")

    def test_failed_or_skipped_stage_stays_blocked(self):
        good = self.staging_ok()
        self.assertEqual(check_staging(good), [])
        for key in ["mysql8_server_verified", "mysql8_integration_executed"]:
            report = self.staging_ok()
            report[key] = False
            self.assertTrue(check_staging(report))
        report = self.staging_ok()
        report["checks"][0]["skipped"] = 1
        self.assertTrue(check_staging(report))
        report = self.staging_ok()
        report["preflight"]["server_version"] = "10.11.4-MariaDB"
        self.assertTrue(check_staging(report))

    def test_missing_operational_evidence_is_blocked(self):
        template = {"toolkit_version": "0.1.2", "operational": {}, "approvals": {}}
        reasons, hashes = check_operational(template, self.root / "evidence.json")
        self.assertEqual(len(reasons), len(REQUIRED_OPERATIONAL) + len(REQUIRED_APPROVERS))
        self.assertEqual(hashes, {})

    def test_forged_evidence_file_paths_outside_folder_are_refused(self):
        outside = self.root.parent / "outside.log"
        outside.write_text("fake", encoding="utf-8")
        self.addCleanup(outside.unlink)
        evidence = {"toolkit_version": "0.1.2", "operational": {"immutable_source": {
            "verified": True, "evidence_file": "../outside.log"}}, "approvals": {}}
        reasons, _ = check_operational(evidence, self.root / "evidence.json")
        self.assertTrue(any("outside directory" in reason for reason in reasons))

    def test_complete_papers_only_allow_review_not_production(self):
        staging = self.root / "stage.json"
        staging.write_text(json.dumps(self.staging_ok()))
        artifact = self.root / "proof.log"
        artifact.write_text("dummy test fixture; independent verification still required")
        data = {
            "toolkit_version": "0.1.2",
            "operational": {name: {"verified": True, "evidence_file": "proof.log"}
                            for name in REQUIRED_OPERATIONAL},
            "approvals": {name: {"verified": True, "evidence_file": "proof.log"}
                          for name in REQUIRED_APPROVERS},
        }
        evidence = self.root / "evidence.json"
        evidence.write_text(json.dumps(data))
        result = assess(staging, evidence)
        self.assertEqual(result["decision"], "review_ready_not_authorized")
        self.assertFalse(result["production_approved"])
        self.assertFalse(result["database_writes_executed"])

    def test_real_local_gate_blocked(self):
        self.assertTrue(check_staging({"status": "blocked", "preflight": None}))

    def test_grants_refuse_global_or_role_privileges(self):
        local = "GRANT ALL PRIVILEGES ON `etlguard_ci`.* TO `etlguard_ci`@`%`"
        basic = "GRANT USAGE ON *.* TO `etlguard_ci`@`%`"
        broad = "GRANT SELECT ON *.* TO `etlguard_ci`@`%`"
        role = "GRANT `reporting_role`@`%` TO `etlguard_ci`@`%`"
        self.assertTrue(grants_are_disposable_schema_only([basic, local], "etlguard_ci"))
        self.assertFalse(grants_are_disposable_schema_only([basic, broad, local], "etlguard_ci"))
        self.assertFalse(grants_are_disposable_schema_only([local, role], "etlguard_ci"))

    def test_shadow_run_validate_never_contacts_database(self):
        plan(self.yaml, self.out, None, False)
        record = run_pilot(self.out / "shadow-manifest.json", False)
        self.assertEqual(record["status"], "validated_only_no_database_writes")
        self.assertEqual(record["rows_valid"], 1)

    def test_shadow_execute_refuses_missing_database_url(self):
        plan(self.yaml, self.out, None, False)
        staging = self.root / "staging.json"
        staging.write_text(json.dumps(self.staging_ok()), encoding="utf-8")
        with patch.dict(os.environ, {"ETLGUARD_SHADOW_MYSQL_URL": ""}):
            with self.assertRaisesRegex(ValueError, "DISPOSABLE shadow"):
                run_pilot(self.out / "shadow-manifest.json", True, "etlguard_shadow_ci",
                          staging)

    def test_shadow_execute_refuses_failed_staging_gate(self):
        plan(self.yaml, self.out, None, False)
        staging = self.root / "staging.json"
        staging.write_text(json.dumps({"status": "blocked", "toolkit_version": "0.1.2"}),
                           encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Staging gate is not fully passed"):
            run_pilot(self.out / "shadow-manifest.json", True, "etlguard_shadow_ci",
                      staging)

    def test_shadow_destination_refuses_live_or_root_url(self):
        okay = "mysql+pymysql://shadow:shadow_pw@127.0.0.1:3308/etlguard_shadow_ci"
        self.assertEqual(check_shadow_destination(okay, "etlguard_shadow_ci").database,
                         "etlguard_shadow_ci")
        for url in ["mysql+pymysql://shadow:pw@localhost/prod",
                    "mysql+pymysql://root:pw@localhost/etlguard_shadow_ci",
                    "mysql+pymysql://shadow:pw@prod-db/etlguard_shadow_ci"]:
            with self.assertRaises(ValueError, msg=url):
                check_shadow_destination(url, "etlguard_shadow_ci")

    def test_ci_bootstrap_requires_consent_and_local_root(self):
        okay = "mysql+pymysql://root:ci_only@127.0.0.1:3306/etlguard_ci"
        self.assertEqual(require_disposable_ci(okay, "DISPOSABLE_MYSQL_ONLY").database,
                         "etlguard_ci")
        for url, consent in [(okay, ""),
                             ("mysql+pymysql://root:pw@production.example/etlguard_ci",
                              "DISPOSABLE_MYSQL_ONLY"),
                             ("mysql+pymysql://root:pw@localhost/production",
                              "DISPOSABLE_MYSQL_ONLY")]:
            with self.assertRaises(ValueError):
                require_disposable_ci(url, consent)

    def test_sql_mode_requires_strict(self):
        self.assertTrue(strict_sql_mode_is_enabled("ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES"))
        self.assertTrue(strict_sql_mode_is_enabled("STRICT_ALL_TABLES"))
        self.assertFalse(strict_sql_mode_is_enabled("NO_ENGINE_SUBSTITUTION"))


if __name__ == "__main__":
    unittest.main()
