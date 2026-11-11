from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from etlguard.cli import app
from etlguard.config import ConfigError, load_config
from etlguard.pipeline import Pipeline, RunResult, resolve_model
from etlguard.profile import profile_csv
from etlguard.reconcile import LoadCounts, ReconciliationError, reconcile
from etlguard.rejects import RejectWriter, RejectedRow

RUNNER = CliRunner()


def write_pipeline(tmp_path: Path, csv_body: str, overrides=None) -> Path:
    (tmp_path / "input.csv").write_text(csv_body, encoding="utf-8")
    settings = {
        "pipeline": "test_fixture",
        "source": {"type": "csv", "path": "input.csv"},
        "model": "etlguard.examples.models:Customer",
        "cleaning": {"date_columns": ["signup_date", "end_date"], "number_columns": ["credit_limit"]},
        "checks": {"unique": ["customer_id"]},
        "target": {"table": "test_fixture", "mode": "upsert", "key": ["customer_id"]},
        "policy": {"fail_if_reject_rate_above": 0.05},
    }
    if overrides:
        settings.update(overrides)
    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(yaml.safe_dump(settings), encoding="utf-8")
    return config_file


VALID_CSV = (
    "customer_id,full_name,email,signup_date,end_date,status,credit_limit\n"
    "C001,Alice Doe,alice@example.com,2026-01-01,,active,1200.00\n"
)
INVALID_CSV = VALID_CSV + "C002,Invalid,email-bad,not-a-date,,active,-20\n"


def test_python_api_returns_typed_result_and_rejects(tmp_path):
    path = write_pipeline(tmp_path, INVALID_CSV)
    result = Pipeline.from_yaml(path).run(dry_run=True)
    assert isinstance(result, RunResult)
    assert result.status == "failed"
    assert result.rows_read == 2
    assert result.rows_valid == 1
    assert result.rows_rejected == 1
    assert result.rows_loaded == 0
    assert result.error_counts_by_field == {"credit_limit": 1, "email": 1, "signup_date": 1}
    rows = list(csv.DictReader(Path(result.reject_csv).open(encoding="utf-8")))
    assert len(rows) == 3
    assert rows[0]["batch_id"] == result.batch_id
    assert rows[0]["raw_row_json"]
    assert json.loads(Path(result.summary_json).read_text())["rows_read"] == 2


def test_good_dry_run_no_database_writes(monkeypatch, tmp_path):
    monkeypatch.delenv("ETLGUARD_DATABASE_URL", raising=False)
    config = write_pipeline(tmp_path, VALID_CSV)
    result = Pipeline.from_yaml(config).run(dry_run=True)
    assert result.status == "dry_run"
    assert result.ok
    assert result.rows_valid == 1
    assert result.rows_loaded == 0
    assert Path(result.reject_csv).read_text().splitlines()[0].startswith("batch_id,")
    assert json.loads(Path(result.reject_json).read_text()) == []


def test_dataset_failure_causes_nonzero(tmp_path):
    config = write_pipeline(tmp_path, VALID_CSV, overrides={
        "checks": {"expected_row_count": {"min": 2}}
    })
    result = Pipeline.from_yaml(config).run(dry_run=True)
    assert result.status == "failed"
    assert result.rows_rejected == 0
    assert result.dataset_issues


def test_cli_validate_exit_codes_and_report(tmp_path):
    valid = write_pipeline(tmp_path, VALID_CSV)
    assert RUNNER.invoke(app, ["validate", str(valid)]).exit_code == 0
    invalid = write_pipeline(tmp_path, INVALID_CSV)
    failed = RUNNER.invoke(app, ["validate", str(invalid)])
    assert failed.exit_code == 1
    assert "Rejected" in failed.output


def test_cli_config_error_is_2(tmp_path):
    file = tmp_path / "bad.yaml"
    file.write_text("source: {type: bogus}")
    assert RUNNER.invoke(app, ["validate", str(file)]).exit_code == 2
    assert RUNNER.invoke(app, ["validate", str(tmp_path / "missing.yaml")]).exit_code == 2
    assert RUNNER.invoke(app, ["run", str(file)]).exit_code == 2


def test_cli_run_missing_environment_is_config_2(tmp_path, monkeypatch):
    monkeypatch.delenv("ETLGUARD_DATABASE_URL", raising=False)
    config = write_pipeline(tmp_path, VALID_CSV)
    result = RUNNER.invoke(app, ["run", str(config)])
    assert result.exit_code == 2


def test_cli_profile(tmp_path):
    input_file = tmp_path / "profile.csv"
    input_file.write_text("amount,email\n1,a@example.com\n2,NA\n3,b@example.com\n")
    data = profile_csv(input_file)
    assert data[0]["inferred_type"] == "number"
    assert data[0]["distinct"] == 3
    assert data[1]["null_pct"] == pytest.approx(1 / 3, abs=0.0001)
    output = RUNNER.invoke(app, ["profile", str(input_file)])
    assert output.exit_code == 0
    assert "amount" in output.output


def test_resolve_model_invalid_and_config_validation(tmp_path):
    with pytest.raises(ConfigError):
        resolve_model("wrong")
    with pytest.raises(ConfigError):
        resolve_model("etlguard.examples.models:NOTAMODEL")
    file = write_pipeline(tmp_path, VALID_CSV, overrides={"unknown": 123})
    with pytest.raises(ConfigError, match="Invalid config"):
        load_config(file)


def test_empty_config_yaml_rejected(tmp_path):
    file = tmp_path / "empty.yaml"
    file.write_text("\n")
    with pytest.raises(ConfigError):
        Pipeline.from_yaml(file)


def test_reconciliation_counts():
    reconcile(3, LoadCounts(2, 1))
    with pytest.raises(ReconciliationError, match="mismatch"):
        reconcile(3, LoadCounts(1, 1))


def test_reject_writer(tmp_path):
    rows = [RejectedRow(
        batch_id="batch", source_row_number=3, field="amount", error_type="decimal_parsing",
        error_message="bad amount", raw_value="abc", raw_row_json='{"amount": "abc"}',
    )]
    csv_path, json_path = RejectWriter().write(tmp_path / "report", rows)
    assert csv_path.exists() and json_path.exists()
    assert list(csv.DictReader(csv_path.open(encoding="utf-8")))[0]["raw_value"] == "abc"
    assert json.loads(json_path.read_text())[0]["field"] == "amount"


def test_sample_messy_feed_has_expected_counts(example_dir):
    path = example_dir / "pipelines" / "customers.yaml"
    result = Pipeline.from_yaml(path).run(dry_run=True)
    assert (result.rows_read, result.rows_valid, result.rows_rejected, result.duplicates_dropped) == (
        8, 4, 3, 1,
    )
    assert result.status == "failed"  # 37.50% exceeds the example 5% policy.


def test_reject_csv_escapes_spreadsheet_formulas_but_json_keeps_raw(tmp_path):
    row = RejectedRow(
        batch_id="batch", source_row_number=2, field="amount", error_type="invalid",
        error_message="Unsafe", raw_value="=1+1", raw_row_json='{"amount": "=1+1"}',
    )
    csv_file, json_file = RejectWriter().write(tmp_path / "formulas", [row])
    assert next(csv.DictReader(csv_file.open(encoding="utf-8")))["raw_value"] == "'=1+1"
    assert json.loads(json_file.read_text())[0]["raw_value"] == "=1+1"
