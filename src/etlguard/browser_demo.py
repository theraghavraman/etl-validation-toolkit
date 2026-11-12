"""Pyodide demo adapter: calls production ValidationEngine; loads valid rows to SQLite."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from etlguard.config import ChecksSettings, CleaningSettings
from etlguard.core import ValidationEngine
from etlguard.examples.models import Customer, Transaction
from etlguard.rejects import REJECT_COLUMNS, _safe_csv_cell
from etlguard.sources.base import Lineage, SourceRecord, validate_headers


def schema_template(selected_model: str = "Customer") -> str:
    """Return a header-only CSV that a visitor can fill using the documented schema."""
    if selected_model not in {"Customer", "Transaction"}:
        raise ValueError("Unknown demonstration model")
    model = Customer if selected_model == "Customer" else Transaction
    buffer = io.StringIO()
    csv.writer(buffer).writerow(list(model.model_fields))
    return buffer.getvalue()


def validate_csv(csv_text: str, selected_model: str = "Customer") -> str:
    if selected_model not in {"Customer", "Transaction"}:
        raise ValueError("Unknown demonstration model")
    model = Customer if selected_model == "Customer" else Transaction
    is_customer = selected_model == "Customer"
    settings = CleaningSettings(
        date_columns=["signup_date", "end_date"] if is_customer else ["transaction_date"],
        number_columns=["credit_limit"] if is_customer else ["amount"],
        lowercase=["email", "status"] if is_customer else ["type"],
        uppercase=["customer_id"] if is_customer else ["customer_id", "transaction_id"],
    )
    checks = ChecksSettings(unique=["customer_id"] if is_customer else ["transaction_id"])
    reader = csv.reader(io.StringIO(csv_text.lstrip("\ufeff")), strict=True)
    headers = validate_headers(next(reader, None))
    batch_id = "browser-demo"

    def records() -> Any:
        for row_number, values in enumerate(reader, start=2):
            if not values:
                values = [""] * len(headers)
            if len(values) > len(headers):
                raise ValueError("A CSV row has more fields than its header")
            if len(values) < len(headers):
                raise ValueError("A CSV row has fewer fields than its header")
            yield SourceRecord(
                dict(zip(headers, values, strict=True)),
                Lineage("browser-upload", row_number, datetime.now(timezone.utc), batch_id),
            )

    core = ValidationEngine(model, settings, checks, Path(".")).run(records())
    if core.source_error:
        raise ValueError(core.source_error)  # Never show a partial CSV as a valid feed.
    valid_rows = []
    for candidate in core.valid:
        valid_rows.append(model.model_validate(candidate.data).model_dump(mode="json"))

    # Browser-only destination: SQLite in Pyodide virtual memory, never MySQL.
    conn = sqlite3.connect(":memory:")
    columns = list(model.model_fields)
    ddl = ", ".join('"' + column + '" TEXT' for column in columns)
    conn.execute(f"CREATE TABLE validated_feed ({ddl})")
    quoted = ", ".join('"' + column + '"' for column in columns)
    slots = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO validated_feed ({quoted}) VALUES ({slots})",
        [[None if row[col] is None else str(row[col]) for col in columns] for row in valid_rows],
    )
    loaded = conn.execute("SELECT COUNT(*) FROM validated_feed").fetchone()[0]
    conn.close()
    reject_rows = [row.model_dump() for row in core.rejects]
    rejects_buffer = io.StringIO()
    writer = csv.DictWriter(rejects_buffer, fieldnames=REJECT_COLUMNS)
    writer.writeheader()
    writer.writerows({key: _safe_csv_cell(value) for key, value in row.items()}
                     for row in reject_rows)
    accepted_buffer = io.StringIO()
    accepted_writer = csv.DictWriter(accepted_buffer, fieldnames=columns)
    accepted_writer.writeheader()
    accepted_writer.writerows({key: _safe_csv_cell(value) for key, value in row.items()}
                             for row in valid_rows)
    return json.dumps({
        "model": selected_model,
        "batch_id": batch_id,
        "rows_read": core.rows_read,
        "rows_cleaned": core.rows_cleaned,
        "rows_valid": core.rows_valid,
        "rows_rejected": core.rows_rejected,
        "duplicates_dropped": core.duplicates_dropped,
        "rows_loaded_sqlite": loaded,
        "error_counts_by_type": core.error_counts_by_type,
        "valid_rows": valid_rows,
        "rejected_rows": reject_rows,
        "rejects_csv": rejects_buffer.getvalue(),
        "valid_csv": accepted_buffer.getvalue(),
    }, ensure_ascii=False, default=str)
