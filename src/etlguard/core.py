"""Shared CSV/Excel/API + Pyodide cleaning/validation orchestration, no SQL imports."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel

from etlguard.checks import DatasetChecker, DatasetIssue
from etlguard.cleaning import Cleaner
from etlguard.config import ChecksSettings, CleaningSettings
from etlguard.rejects import RejectedRow, reject
from etlguard.sources.base import SourceRecord
from etlguard.validation import Candidate, RowValidator


@dataclass
class CoreResult:
    rows_read: int = 0
    source_digest: str = ""
    source_error: str | None = None
    rows_cleaned: int = 0
    duplicates_dropped: int = 0
    valid: list[Candidate] = field(default_factory=list)
    rejects: list[RejectedRow] = field(default_factory=list)
    dataset_issues: list[DatasetIssue] = field(default_factory=list)
    rejected_source_rows: set[tuple[str, int]] = field(default_factory=set)

    @property
    def rows_valid(self) -> int:
        return len(self.valid)

    @property
    def rows_rejected(self) -> int:
        return len(self.rejected_source_rows)

    @property
    def error_counts_by_type(self) -> dict[str, int]:
        counts = Counter(row.error_type for row in self.rejects)
        counts.update(issue.error_type for issue in self.dataset_issues)
        return dict(sorted(counts.items()))

    @property
    def error_counts_by_field(self) -> dict[str, int]:
        counts = Counter(row.field for row in self.rejects)
        counts.update(issue.field for issue in self.dataset_issues)
        return dict(sorted(counts.items()))


class ValidationEngine:
    def __init__(
        self,
        model: type[BaseModel],
        cleaning: CleaningSettings,
        checks: ChecksSettings,
        base_dir: Path = Path("."),
    ):
        self.cleaner = Cleaner(cleaning)
        self.validator = RowValidator(model)
        self.checker = DatasetChecker(checks, base_dir, drop_duplicates=cleaning.drop_duplicates)

    def run(self, source: Iterable[SourceRecord]) -> CoreResult:
        result = CoreResult()
        candidates: list[Candidate] = []
        clean_rows: list[dict[str, Any]] = []
        fingerprint = sha256()
        def guarded_source() -> Iterable[SourceRecord]:
            try:
                yield from source
            except Exception as exc:
                # Preserve all already-read counts/rejects for a failed partial feed.
                result.source_error = f"source read failed: {type(exc).__name__}: {exc}"

        for record in guarded_source():
            result.rows_read += 1
            # Include ALL incoming records, including invalid ones, in the retry identity.
            # A length prefix avoids ambiguous concatenations of adjacent JSON records.
            encoded = json.dumps({
                "row_number": record.lineage.source_row_number,
                "data": record.data,
            }, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            fingerprint.update(len(encoded).to_bytes(8, "big"))
            fingerprint.update(encoded)
            try:
                cleaned = self.cleaner.clean(record.data)
            except ValueError as exc:
                result.rejects.append(reject(
                    record.lineage.batch_id, record.lineage.source_row_number,
                    "__row__", "cleaning", str(exc), record.data, record.data,
                ))
                result.rejected_source_rows.add(
                    (record.lineage.source_name, record.lineage.source_row_number)
                )
                continue
            result.rows_cleaned += 1
            clean_rows.append(cleaned)
            outcome = self.validator.validate(cleaned, record.data, record.lineage)
            if outcome.rejects:
                # Do not deduplicate invalid rows: every physical bad source
                # record must count toward rejection thresholds and audit totals.
                result.rejects.extend(outcome.rejects)
                result.rejected_source_rows.add(
                    (record.lineage.source_name, record.lineage.source_row_number)
                )
            elif outcome.candidate:
                candidates.append(outcome.candidate)
        result.valid, dataset_rejects = self.checker.filter_candidates(candidates)
        result.duplicates_dropped = self.checker.duplicates_dropped
        result.rejects.extend(dataset_rejects)
        # The same physical source row can produce multiple errors, but is one reject.
        rejected_numbers = {row.source_row_number for row in dataset_rejects}
        for candidate in candidates:
            if candidate.lineage.source_row_number in rejected_numbers:
                result.rejected_source_rows.add((candidate.lineage.source_name, candidate.lineage.source_row_number))
        result.dataset_issues = self.checker.dataset_issues(clean_rows)
        result.source_digest = fingerprint.hexdigest()
        assert result.rows_read == result.rows_valid + result.rows_rejected + result.duplicates_dropped
        return result
