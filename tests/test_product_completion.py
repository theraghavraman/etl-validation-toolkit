"""Product regressions: usable installed CLI and lossless tabular/decimal contracts."""
from __future__ import annotations

import csv
import io
import json
from decimal import Decimal

import pytest
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, MetaData, Table, select
from typer.testing import CliRunner

from etlguard import __version__
from etlguard.browser_demo import schema_template, validate_csv
from etlguard.cli import app
from etlguard.config import TargetSettings
from etlguard.loaders.mysql import MysqlLoader, build_tables
from etlguard.pipeline import Pipeline
from etlguard.sources.base import lineage
from etlguard.validation import Candidate


def test_installed_cli_version_and_initialized_pipeline(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["--version"]).output.strip() == f"ETLGuard {__version__}"
    path = tmp_path / "example"
    assert runner.invoke(app, ["init", str(path)]).exit_code == 0
    result = Pipeline.from_yaml(path / "pipeline.yaml").run(dry_run=True)
    assert (result.ok, result.rows_valid) == (True, 3)
    assert (path / "reports").is_dir()
    marker = path / "do-not-touch.txt"
    marker.write_text("original")
    assert runner.invoke(app, ["init", str(path)]).exit_code == 2
    assert marker.read_text() == "original"


def test_malformed_source_fails_whole_batch_before_loading(tmp_path, monkeypatch):
    runner = CliRunner()
    path = tmp_path / "example"
    assert runner.invoke(app, ["init", str(path)]).exit_code == 0
    data = path / "data/customers_clean.csv"
    lines = data.read_text().splitlines()
    data.write_text(lines[0] + "\nextra," + lines[1] + "\n")
    called = []
    monkeypatch.setattr(MysqlLoader, "load", lambda *a, **k: called.append(True))
    result = Pipeline.from_yaml(path / "pipeline.yaml").run()
    assert result.status == "failed" and result.rows_loaded == 0
    assert any("fields" in failure for failure in result.failures)
    assert not called


def test_browser_exports_valid_rows_and_header_template(example_dir):
    text = (example_dir / "data/customers_clean.csv").read_text()
    text = text.replace("Amina Khan", "=SUM(1,2)")
    # Quoted input contains a comma; the accepted value must remain one cell.
    text = text.replace("=SUM(1,2)", '"=SUM(1,2)"')
    result = json.loads(validate_csv(text))
    rows = list(csv.DictReader(io.StringIO(result["valid_csv"])))
    assert len(rows) == 3
    assert rows[0]["full_name"] == "'=SUM(1,2)"
    assert result["valid_rows"][0]["full_name"] == "=SUM(1,2)"
    assert "transaction_id" in schema_template("Transaction")
    with pytest.raises(ValueError, match="Unknown"):
        schema_template("absent")


class PreciseAmount(BaseModel):
    item_id: str = Field(max_length=20)
    amount: Decimal = Field(max_digits=12, decimal_places=4)


class DefaultAmount(BaseModel):
    item_id: str = Field(max_length=20)
    amount: Decimal


def precise_candidate(model, value):
    data = model(item_id="item-a", amount=value).model_dump()
    return Candidate(data, data, data, lineage("test", 2, "batch"))


def test_decimal_model_constraints_define_database_precision():
    target, _ = build_tables(PreciseAmount, TargetSettings(table="precise"))
    assert (target.c.amount.type.precision, target.c.amount.type.scale) == (12, 4)
    MysqlLoader._check_decimal_storage([precise_candidate(PreciseAmount, "0.1234")], target)


@pytest.mark.parametrize("value", ["0.1234", "10000000000000000.00", "NaN"])
def test_decimal_storage_refuses_rounding_overflow_or_nonfinite(value):
    target, _ = build_tables(DefaultAmount, TargetSettings(table="legacy"))
    # Low-level direct candidates are still checked before database writes.
    data = {"item_id": "a", "amount": Decimal(value)}
    candidate = Candidate(data, data, data, lineage("test", 2, "batch"))
    with pytest.raises(ValueError, match="decimal|Decimal"):
        MysqlLoader._check_decimal_storage([candidate], target)


def test_zero_and_trailing_zero_decimals_fit_existing_storage():
    target, _ = build_tables(DefaultAmount, TargetSettings(table="legacy"))
    MysqlLoader._check_decimal_storage([
        precise_candidate(DefaultAmount, "0.00000"),
        precise_candidate(DefaultAmount, "1.2300"),
    ], target)


@pytest.mark.integration
def test_mysql_decimal_four_places_round_trips_without_rounding(tmp_path):
    import os
    from uuid import uuid4
    from sqlalchemy import text
    url = os.getenv("ETLGUARD_TEST_MYSQL_URL")
    if not url:
        pytest.skip("Set ETLGUARD_TEST_MYSQL_URL for MySQL integration")
    engine = create_engine(url)
    target = TargetSettings(table="it_precision_" + uuid4().hex[:8], mode="append")
    try:
        loader = MysqlLoader(engine=engine)
        assert loader.load([precise_candidate(PreciseAmount, "0.1234")],
                           str(uuid4()), PreciseAmount, target).loaded == 1
        table = Table(target.table, MetaData(), autoload_with=engine)
        with engine.connect() as conn:
            assert conn.execute(select(table.c.amount)).scalar_one() == Decimal("0.1234")
        # Existing narrower schemas must also reject, rather than round on a later load.
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE `{target.table}` MODIFY amount DECIMAL(12,2)"))
        with pytest.raises(ValueError, match="refusing silent rounding"):
            loader.load([precise_candidate(PreciseAmount, "0.5678")],
                        str(uuid4()), PreciseAmount, target)
    finally:
        with engine.begin() as conn:
            for name in [f"stg_{target.table}", target.table]:
                conn.execute(text(f"DROP TABLE IF EXISTS `{name}`"))
        engine.dispose()
