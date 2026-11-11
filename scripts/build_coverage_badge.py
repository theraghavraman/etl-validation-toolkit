"""Render Shields endpoint JSON from the branch-aware coverage.py summary.

Usage: python scripts/build_coverage_badge.py coverage-summary.json demo/coverage.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def badge_payload(total_percent: float) -> dict[str, str | int]:
    value = max(0, min(100, round(total_percent)))
    if value >= 90:
        color = "brightgreen"
    elif value >= 85:
        color = "green"
    elif value >= 75:
        color = "yellow"
    else:
        color = "red"
    return {"schemaVersion": 1, "label": "coverage", "message": f"{value}%", "color": color}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: build_coverage_badge.py coverage-summary.json demo/coverage.json")
    source, destination = Path(sys.argv[1]), Path(sys.argv[2])
    summary = json.loads(source.read_text(encoding="utf-8"))
    percent = float(summary["totals"]["percent_covered"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(badge_payload(percent), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
