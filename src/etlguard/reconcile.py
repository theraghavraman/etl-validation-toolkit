"""ETL count reconciliation; a failure aborts the database transaction."""

from __future__ import annotations

from dataclasses import dataclass, field


class ReconciliationError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadCounts:
    inserted: int
    updated: int
    unchanged: int = 0
    versions_created: int = 0
    replayed: bool = field(default=False, compare=False)

    @property
    def loaded(self) -> int:
        # "updated" means an existing business key was processed; for SCD2 it
        # includes unchanged matches, with unchanged disclosed separately.
        return self.inserted + self.updated


def reconcile(rows_valid: int, counts: LoadCounts) -> None:
    if rows_valid != counts.loaded:
        raise ReconciliationError(
            f"reconciliation mismatch: valid={rows_valid}, "
            f"inserted={counts.inserted}, updated={counts.updated}"
        )
