"""Excel reader using pandas/openpyxl, with physical worksheet lineage."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from etlguard.config import SourceSettings
from etlguard.sources.base import SourceRecord, lineage


class ExcelSource:
    def __init__(self, settings: SourceSettings, base_dir: Path):
        if settings.path is None:
            raise ValueError("Excel source missing path")
        self.settings = settings
        self.path = (base_dir / settings.path).resolve()

    def read(self, batch_id: str) -> Iterator[SourceRecord]:
        import pandas as pd

        cfg = self.settings
        frame = pd.read_excel(
            self.path,
            sheet_name=cfg.sheet,
            header=cfg.header_row - 1,
            skiprows=cfg.skip_rows,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            engine="openpyxl",
        )
        # With numeric skiprows, pandas' header is relative to the remaining sheet.
        first_data_row = cfg.skip_rows + cfg.header_row + 1
        for offset, (_, series) in enumerate(frame.iterrows()):
            record = {str(key): value for key, value in series.items()}
            yield SourceRecord(
                record,
                lineage(cfg.name or self.path.name, first_data_row + offset, batch_id),
            )
