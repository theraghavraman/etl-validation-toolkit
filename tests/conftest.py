"""Shared synthetic data and strongly typed source records."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from etlguard.sources.base import Lineage, SourceRecord


@pytest.fixture
def customer_raw() -> dict[str, Any]:
    return {
        "customer_id": "C001", "full_name": "Alice Doe", "email": "alice@example.com",
        "signup_date": "2026-01-01", "end_date": None,
        "status": "active", "credit_limit": "1200.00",
    }


@pytest.fixture
def record_factory():
    def create(data: dict[str, Any], number: int = 2) -> SourceRecord:
        line = Lineage("fixture.csv", number, datetime.now(timezone.utc), "batch-123")
        return SourceRecord(data, line)
    return create


@pytest.fixture
def example_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "etlguard" / "examples"
