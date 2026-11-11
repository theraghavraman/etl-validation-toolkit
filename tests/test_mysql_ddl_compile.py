"""Compile critical MySQL DDL offline; NOT a substitute for MySQL 8 execution."""
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects.mysql import dialect

from etlguard.audit import audit_table, commits_table
from etlguard.config import TargetSettings
from etlguard.examples.models import Customer
from etlguard.loaders.mysql import build_tables


def test_scd2_generated_active_marker_compiles_for_mysql8():
    target, stage = build_tables(
        Customer, TargetSettings(table="pilot_customer", mode="scd2", key=["customer_id"])
    )
    ddl = str(CreateTable(target).compile(dialect=dialect()))
    assert "__etl_current_marker SMALLINT GENERATED ALWAYS AS" in ddl
    assert "UNIQUE (customer_id, __etl_current_marker)" in ddl
    assert "ENGINE=InnoDB" in ddl
    assert "DATETIME(6)" in ddl
    assert "__etl_batch_id VARCHAR(36) NOT NULL" in str(
        CreateTable(stage).compile(dialect=dialect())
    )


def test_upsert_requires_mysql_unique_business_key_ddl():
    target, _stage = build_tables(
        Customer, TargetSettings(table="pilot_customer", mode="upsert", key=["customer_id"])
    )
    ddl = str(CreateTable(target).compile(dialect=dialect()))
    assert "UNIQUE (customer_id)" in ddl
    assert "ENGINE=InnoDB" in ddl


def test_audit_and_commit_ledger_require_innodb():
    for table in (audit_table(), commits_table()):
        ddl = str(CreateTable(table).compile(dialect=dialect()))
        assert "ENGINE=InnoDB" in ddl
        assert "batch_id VARCHAR(36) NOT NULL" in ddl
