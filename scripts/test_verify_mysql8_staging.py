"""Offline safety regression tests for the independent staging gate."""
import unittest
from pathlib import Path
import importlib.util

SCRIPT = Path(__file__).with_name("verify_mysql8_staging.py")
spec = importlib.util.spec_from_file_location("etlguard_staging_gate", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TestSafetyGate(unittest.TestCase):
    good = "mysql+pymysql://etlguard_ci:local_only@127.0.0.1:3307/etlguard_ci"

    def test_safe_disposable_database(self):
        url = module.validate_destination(self.good, "etlguard_ci")
        self.assertEqual(url.database, "etlguard_ci")

    def test_production_style_name_blocked(self):
        with self.assertRaises(ValueError):
            module.validate_destination(self.good.replace("/etlguard_ci", "/customers"), "customers")

    def test_mismatch_blocked(self):
        with self.assertRaises(ValueError):
            module.validate_destination(self.good, "etlguard_test")

    def test_root_blocked(self):
        with self.assertRaises(ValueError):
            module.validate_destination(self.good.replace("etlguard_ci:local", "root:local"), "etlguard_ci")

    def test_remote_blocked_without_override(self):
        remote = self.good.replace("127.0.0.1", "db.example.invalid")
        with self.assertRaises(ValueError):
            module.validate_destination(remote, "etlguard_ci")
        self.assertEqual(module.validate_destination(remote, "etlguard_ci", True).host,
                         "db.example.invalid")

    def test_mysql8_only(self):
        self.assertTrue(module.is_mysql8("8.0.41"))
        self.assertTrue(module.is_mysql8("8.4.4"))
        self.assertFalse(module.is_mysql8("5.7.42"))
        self.assertFalse(module.is_mysql8("10.11.2-MariaDB"))

    def test_only_temporary_integration_tables_allowed(self):
        self.assertEqual(module.disallowed_tables([
            "etl_run_audit", "etl_batch_commits", "it_abc", "stg_it_abc", "customers"
        ]), ["customers"])


if __name__ == "__main__":
    unittest.main()
