"""Quick per-column quality profile for ad-hoc feed triage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def profile_csv(path: str | Path, encoding: str = "utf-8-sig") -> list[dict[str, Any]]:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding)
    blank = {"", "na", "n/a", "null", "-"}
    profiles = []
    for column in frame.columns:
        series = frame[column].astype("string").str.strip()
        series = series.mask(series.str.casefold().isin(blank))
        nonnull = series.dropna()
        numbers = pd.to_numeric(nonnull.str.replace(",", "", regex=False), errors="coerce")
        all_numeric = len(nonnull) > 0 and numbers.notna().all()
        profiles.append({
            "column": str(column),
            "inferred_type": "number" if all_numeric else "string",
            "rows": int(len(series)),
            "null_pct": round(float(series.isna().mean()), 4) if len(series) else 0.0,
            "distinct": int(nonnull.nunique()),
            "min": (float(numbers.min()) if all_numeric else str(nonnull.min())) if len(nonnull) else None,
            "max": (float(numbers.max()) if all_numeric else str(nonnull.max())) if len(nonnull) else None,
        })
    return profiles
