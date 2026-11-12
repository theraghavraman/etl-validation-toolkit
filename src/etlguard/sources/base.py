"""Source protocol and per-record lineage shared across all connectors."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol


@dataclass(frozen=True)
class Lineage:
    source_name: str
    source_row_number: int
    ingested_at: datetime
    batch_id: str


@dataclass(frozen=True)
class SourceRecord:
    data: dict[str, Any]
    lineage: Lineage


class Source(Protocol):
    def read(self, batch_id: str) -> Iterator[SourceRecord]: ...


def lineage(source: str, row: int, batch_id: str) -> Lineage:
    return Lineage(source, row, datetime.now(timezone.utc), batch_id)


def validate_headers(headers: Sequence[str] | None) -> list[str]:
    """Reject ambiguous tabular headers before values can be discarded or renamed."""
    if not headers or any(not name.strip() for name in headers):
        raise ValueError("A non-empty header is required for every column")
    if len(headers) != len(set(headers)):
        raise ValueError("Duplicate column headers are not allowed")
    return list(headers)
