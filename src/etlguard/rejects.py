"""One CSV/JSON record *per validation error*, preserving original source values."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from etlguard.cleaning import snake_case

REJECT_COLUMNS = [
    "batch_id", "source_row_number", "field", "error_type",
    "error_message", "raw_value", "raw_row_json",
]


class RejectedRow(BaseModel):
    batch_id: str
    source_row_number: int
    field: str
    error_type: str
    error_message: str
    raw_value: str | None
    raw_row_json: str


def raw_json(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)


def reject(
    batch_id: str,
    source_row_number: int,
    field: str,
    error_type: str,
    error_message: str,
    raw_row: dict[str, Any],
    cleaned_row: dict[str, Any],
) -> RejectedRow:
    # Preserve the original incoming cell whenever possible. Model field names
    # may have been normalized/renamed, so fall back to the cleaned value.
    raw_value = raw_row.get(field)
    if field not in raw_row:
        for incoming_name, original in raw_row.items():
            normalized = snake_case(str(incoming_name))
            if normalized == field:
                raw_value = original
                break
        else:
            raw_value = cleaned_row.get(field)
    return RejectedRow(
        batch_id=batch_id,
        source_row_number=source_row_number,
        field=field,
        error_type=error_type,
        error_message=error_message,
        raw_value=None if raw_value is None else str(raw_value),
        raw_row_json=raw_json(raw_row),
    )


def _safe_csv_cell(value: Any) -> Any:
    """Prevent spreadsheet-formula execution without changing exact JSON rejects."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


class RejectWriter:
    def write(self, path_prefix: Path, errors: list[RejectedRow]) -> tuple[Path, Path]:
        path_prefix.parent.mkdir(parents=True, exist_ok=True)
        csv_path = path_prefix.with_name(path_prefix.name + "_rejects.csv")
        json_path = path_prefix.with_name(path_prefix.name + "_rejects.json")
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=REJECT_COLUMNS)
            writer.writeheader()
            for row in errors:
                writer.writerow({key: _safe_csv_cell(value)
                                 for key, value in row.model_dump().items()})
        json_path.write_text(
            json.dumps([item.model_dump() for item in errors], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return csv_path, json_path
