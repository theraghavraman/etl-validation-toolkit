"""Retain synthetic staging evidence in Actions logs when artifact storage is full."""

from base64 import b64encode
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main() -> None:
    root = Path.cwd()
    paths = [root / "staging-gate-report.json", root / "shadow-smoke-report.json"]
    paths.extend(sorted((root / "mysql8-gate-logs").glob("*")))
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for path in paths:
            if path.is_file() and not path.is_symlink():
                archive.write(path, path.relative_to(root))
    print("ETLGUARD_EVIDENCE_BASE64=" + b64encode(buffer.getvalue()).decode("ascii"))


if __name__ == "__main__":
    main()
