# Application completion and verification — 11 November 2026

The application is implemented, packaged, and available locally. On macOS,
double-click `Launch ETL Guard.command`. On other platforms, run
`python scripts/serve_demo.py` from the extracted source directory. The launcher
binds to localhost and chooses an available port. Browser startup downloads
Pyodide, Pydantic and SQLite; CSV processing stays in the browser.

## Completed work

- Fixed browser startup by explicitly loading SQLite in Pyodide.
- Cleared obsolete metrics and downloads after invalid uploads and validation failures.
- Added empty-file and file-read error handling and useful Python error messages.
- Applied the CLI's spreadsheet formula escaping to browser CSV exports while preserving raw JSON.
- Updated the displayed application version and configured the real private repository URL.
- Fixed lint and type failures, including typed CLI arguments and SQLAlchemy schema items.
- Constrained NumPy and pandas stubs to retain the declared Python 3.11 compatibility.
- Accepted MySQL's escaped schema identifiers in scoped grants and explicitly aliased metadata columns.
- Added a local launcher and included scripts, documentation and demo assets in source distributions.
- Preserved CI evidence in job logs when the account's Actions artifact storage was full.

## Verification evidence

Runtime commit: `a5ca4bd0041e043d704c5ea9c15d58381ae03860`.

- [Standard CI](https://github.com/theraghavraman/etl-validation-toolkit/actions/runs/43061540787): passed.
- [MySQL staging matrix](https://github.com/theraghavraman/etl-validation-toolkit/actions/runs/43061543916): all four jobs passed.
- MySQL 8.0.46 and 8.4.13, each with Python 3.11 and 3.13: integration tests, complete suite, Ruff and mypy passed with zero skipped tests.
- Every matrix job passed the isolated shadow load, independent source-to-target value comparison, audit/ledger checks, staging cleanup, identical retry and unchanged physical target fingerprint.
- Locally: 39 script tests passed; 123 application tests passed, with 13 database tests deferred to the successful remote matrix. Coverage exceeded the required 85%.
- Browser preview: Customer sample produced 8 read / 4 accepted / 3 rejected / 1 duplicate; Transaction produced 6 read / 3 accepted / 2 rejected / 1 duplicate. Accepted records loaded into browser SQLite.
- The wheel installed in a fresh environment and validated all 3 bundled clean sample records. Wheel and source distributions built successfully; source contents were checked for launcher, demo and reconciliation scripts.

The `etlguard-verification-evidence.zip` delivery contains all four actual staging
reports, shadow reports and quality logs recovered from the successful job logs.
The repository is private. GitHub Actions artifact uploads remain subject to the
account's storage quota; no existing artifacts were deleted.

## Operational boundary

The evidence covers synthetic data in disposable MySQL instances. Production
backup/restore, performance and recovery objectives, monitoring, and approval by
the actual data, database and operations owners require the intended production
environment. Those approvals have not been invented or marked complete.
