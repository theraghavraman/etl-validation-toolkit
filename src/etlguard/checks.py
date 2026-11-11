"""Dataset-level checks and reference-data checks independent of the database."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etlguard.config import ChecksSettings, ForeignKeySettings
from etlguard.rejects import RejectedRow, raw_json, reject
from etlguard.validation import Candidate


@dataclass(frozen=True)
class DatasetIssue:
    field: str
    error_type: str
    message: str


def load_reference(ref: ForeignKeySettings, base_dir: Path) -> set[str]:
    if ref.values is not None:
        return {str(value) for value in ref.values}
    if ref.path is None or ref.column is None:
        raise ValueError("missing foreign key reference settings")
    path = (base_dir / ref.path).resolve()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or ref.column not in reader.fieldnames:
            raise ValueError(f"Reference file {path} missing column {ref.column}")
        return {str(row[ref.column]) for row in reader if row[ref.column] is not None}


class DatasetChecker:
    def __init__(self, settings: ChecksSettings, base_dir: Path, drop_duplicates: bool = False):
        self.settings = settings
        self.drop_duplicates = drop_duplicates
        self.duplicates_dropped = 0
        self.references = {
            field: load_reference(ref, base_dir) for field, ref in settings.foreign_keys.items()
        }

    def filter_candidates(self, items: list[Candidate]) -> tuple[list[Candidate], list[RejectedRow]]:
        valid: list[Candidate] = []
        rejected: list[RejectedRow] = []
        seen: set[tuple[Any, ...]] = set()
        seen_valid_rows: set[str] = set()
        self.duplicates_dropped = 0
        for item in items:
            problems: list[RejectedRow] = []
            for field, reference in self.references.items():
                value = item.data.get(field)
                if value is not None and str(value) not in reference:
                    problems.append(reject(
                        item.lineage.batch_id, item.lineage.source_row_number,
                        field, "foreign_key", "value not present in reference set",
                        item.raw, item.cleaned,
                    ))
            # Only previously ACCEPTED records can justify de-duplicating a row.
            # A row with invalid foreign keys or a duplicate business key must
            # remain an independently counted reject, even when identical to
            # another rejected row.
            fingerprint = raw_json(item.cleaned)
            if not problems and self.drop_duplicates and fingerprint in seen_valid_rows:
                self.duplicates_dropped += 1
                continue
            if self.settings.unique:
                key = tuple(item.data.get(col) for col in self.settings.unique)
                if key in seen:
                    problems.append(reject(
                        item.lineage.batch_id, item.lineage.source_row_number,
                        ",".join(self.settings.unique), "unique", "duplicate dataset business key",
                        item.raw, item.cleaned,
                    ))
                elif not problems:
                    seen.add(key)
            if problems:
                rejected.extend(problems)
            else:
                valid.append(item)
                seen_valid_rows.add(fingerprint)
        return valid, rejected

    def dataset_issues(self, clean_rows: list[dict[str, Any]]) -> list[DatasetIssue]:
        issues: list[DatasetIssue] = []
        count = len(clean_rows)
        bounds = self.settings.expected_row_count
        if bounds:
            if bounds.min is not None and count < bounds.min:
                issues.append(DatasetIssue("__dataset__", "row_count", f"{count} below minimum {bounds.min}"))
            if bounds.max is not None and count > bounds.max:
                issues.append(DatasetIssue("__dataset__", "row_count", f"{count} above maximum {bounds.max}"))
        for field, threshold in self.settings.max_null_pct.items():
            if not count:
                continue
            nulls = sum(row.get(field) is None for row in clean_rows)
            pct = nulls / count
            if pct > threshold:
                issues.append(DatasetIssue(
                    field, "null_pct", f"{pct:.2%} null exceeds limit {threshold:.2%}",
                ))
        return issues
