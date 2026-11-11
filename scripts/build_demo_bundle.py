"""Bundle only MySQL-free, pandas-free production Python modules for GitHub Pages."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "etlguard"
DEST = ROOT / "demo" / "etlguard_core.zip"
FILES = [
    "__init__.py", "config.py", "cleaning.py", "validation.py", "checks.py",
    "rejects.py", "core.py", "browser_demo.py",
    "sources/__init__.py", "sources/base.py", "sources/api.py", "sources/csv.py",
    "sources/excel.py", "examples/__init__.py", "examples/models.py",
]
# sources/__init__.py is not import-safe in the browser because it imports httpx/pandas;
# explicitly provide the lightweight package init instead of its desktop variant.
with ZipFile(DEST, "w", compression=ZIP_DEFLATED) as archive:
    for name in FILES:
        if name == "sources/__init__.py":
            archive.writestr("etlguard/sources/__init__.py", '"""Shared browser-safe lineage package."""\n')
        else:
            archive.write(SRC / name, f"etlguard/{name}")
print(f"Created {DEST} ({DEST.stat().st_size:,} bytes)")
