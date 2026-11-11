"""Pydantic v2 typed row validation, preserving all independent field errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from etlguard.rejects import RejectedRow, reject
from etlguard.sources.base import Lineage


@dataclass
class Candidate:
    data: dict[str, Any]
    cleaned: dict[str, Any]
    raw: dict[str, Any]
    lineage: Lineage


@dataclass
class ValidationOutcome:
    candidate: Candidate | None
    rejects: list[RejectedRow]


class RowValidator:
    def __init__(self, model: type[BaseModel]):
        self.model = model

    def validate(self, cleaned: dict[str, Any], raw: dict[str, Any], line: Lineage) -> ValidationOutcome:
        try:
            # Retain Python date/Decimal objects for correct SQLAlchemy type binding.
            instance = self.model.model_validate(cleaned)
            return ValidationOutcome(
                Candidate(instance.model_dump(mode="python"), cleaned, raw, line), [],
            )
        except ValidationError as exc:
            errors = []
            for detail in exc.errors(include_url=False, include_context=False, include_input=False):
                loc = ".".join(str(part) for part in detail["loc"]) or "__row__"
                errors.append(reject(
                    line.batch_id, line.source_row_number, loc, detail["type"],
                    detail["msg"], raw, cleaned,
                ))
            return ValidationOutcome(None, errors)
