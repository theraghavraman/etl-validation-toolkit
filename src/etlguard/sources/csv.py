"""Streaming CSV reader that preserves cells, blank records and logical lineage."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from etlguard.config import SourceSettings
from etlguard.sources.base import SourceRecord, lineage, validate_headers


class CsvSource:
    def __init__(self, settings: SourceSettings, base_dir: Path):
        if settings.path is None:
            raise ValueError("CSV source missing path")
        self.settings = settings
        self.path = (base_dir / settings.path).resolve()

    def read(self, batch_id: str) -> Iterator[SourceRecord]:
        cfg = self.settings
        with self.path.open(encoding=cfg.encoding, newline="") as handle:
            reader = csv.reader(handle, delimiter=cfg.delimiter, strict=True)
            for _ in range(cfg.header_row - 1):
                next(reader, None)
            headers = validate_headers(next(reader, None))
            row_number = cfg.header_row
            while True:
                for _ in range(cfg.chunksize):
                    values = next(reader, None)
                    if values is None:
                        return
                    row_number += 1
                    # A blank physical record still counts toward the reject policy.
                    if not values:
                        values = [""] * len(headers)
                    if len(values) != len(headers):
                        raise ValueError(
                            f"CSV row {row_number} has {len(values)} fields; "
                            f"its header has {len(headers)}"
                        )
                    yield SourceRecord(
                        dict(zip(headers, values, strict=True)),
                        lineage(cfg.name or self.path.name, row_number, batch_id),
                    )
