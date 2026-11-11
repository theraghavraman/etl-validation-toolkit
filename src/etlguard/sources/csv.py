"""Chunked pandas CSV reader that preserves raw strings and blank rows."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from etlguard.config import SourceSettings
from etlguard.sources.base import SourceRecord, lineage


class CsvSource:
    def __init__(self, settings: SourceSettings, base_dir: Path):
        if settings.path is None:
            raise ValueError("CSV source missing path")
        self.settings = settings
        self.path = (base_dir / settings.path).resolve()

    def read(self, batch_id: str) -> Iterator[SourceRecord]:
        import pandas as pd

        cfg = self.settings
        reader = pd.read_csv(
            self.path,
            sep=cfg.delimiter,
            encoding=cfg.encoding,
            header=cfg.header_row - 1,
            chunksize=cfg.chunksize,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            skip_blank_lines=False,
        )
        row_number = cfg.header_row
        for chunk in reader:
            for row in chunk.to_dict(orient="records"):
                row_number += 1
                yield SourceRecord(row, lineage(cfg.name or self.path.name, row_number, batch_id))
