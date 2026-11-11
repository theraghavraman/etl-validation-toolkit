"""Deterministic, idempotent record cleaning; no pandas or database dependency."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from etlguard.config import CleaningSettings


def snake_case(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value)
    return value.strip("_").lower()


def date_iso(value: Any, formats: list[str]) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return value
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value  # Pydantic reports a bad date, rather than silently dropping it.


def decimal_number(value: Any) -> Any:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return value
    number = str(value).strip()
    if number.startswith("(") and number.endswith(")"):
        number = "-" + number[1:-1]
    number = re.sub(r"[,$€£₹\s]", "", number)
    try:
        parsed = Decimal(number)
        return parsed if parsed.is_finite() else value
    except InvalidOperation:
        return value


class Cleaner:
    def __init__(self, rules: CleaningSettings):
        self.rules = rules
        self.null_tokens = {token.strip().casefold() for token in rules.null_tokens}
        # Rename rules use normalized names only when normalization is enabled.
        # Without snake_case_columns, mapping keys must match raw column names.
        name_format = snake_case if rules.snake_case_columns else str
        self.rename = {name_format(key): name_format(val) for key, val in rules.rename_map.items()}
        self.dates = set(rules.date_columns)
        self.numbers = set(rules.number_columns)
        self.lowercase = set(rules.lowercase)
        self.uppercase = set(rules.uppercase)

    def clean(self, row: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for raw_name, raw_value in row.items():
            base = snake_case(str(raw_name)) if self.rules.snake_case_columns else str(raw_name)
            name = self.rename.get(base, base)
            value = raw_value.strip() if self.rules.trim and isinstance(raw_value, str) else raw_value
            if isinstance(value, str) and value.casefold() in self.null_tokens:
                value = None
            if value is not None and name in self.dates:
                value = date_iso(value, self.rules.date_formats)
            if value is not None and name in self.numbers:
                value = decimal_number(value)
            if isinstance(value, str) and name in self.lowercase:
                value = value.lower()
            if isinstance(value, str) and name in self.uppercase:
                value = value.upper()
            if name in result:
                raise ValueError(f"Two incoming columns normalise to the same name: {name}")
            result[name] = value
        return result
