# ETL Guard · `etl-validation-toolkit`

[![CI](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Ftheraghavraman.github.io%2Fetl-validation-toolkit%2Fcoverage.json)](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)

**Data quality gates, row rejects, and SCD Type 2 history for everyday ETL jobs.**

> **Portfolio project:** Synthetic example data only. The repository is independently developed and does not contain client datasets, credentials, or proprietary code. The source repository is private; the browser application can be launched locally.

## The 30-second problem statement

A scheduled job can succeed technically while loading the wrong records: a CSV has an extra space in its business key, a REST API returns an invalid date, a spreadsheet contains duplicate customers, or a nightly merge drops rows. Those are the same issues monitored with source/target row counts, rejected rows, business keys, and SCD Type 1/2 in enterprise ETL operations.

**ETL Guard makes those controls repeatable in Python.** Define a source, cleaning rules, a typed Pydantic model, dataset checks, a target load mode, and an error threshold in YAML. Every source row keeps batch lineage. Rejected rows become actionable CSV/JSON reports; accepted records can be staged and transactionally merged into MySQL 8. A count mismatch rolls back the load. Since v0.1.2, the success audit and batch-commit ledger commit atomically with the target data, enabling safe same-ID retries.

[Launch the browser application](#launch-the-browser-application) · [Read the configuration guide](docs/configuration.md) · [Production v0.1.2 runbook](docs/production-v0.1.2.md) · [Browse release instructions](docs/deployment.md)

## Launch the browser application

On macOS, double-click **Launch ETL Guard.command** in this folder. It opens the
application in your browser using an available local port. Keep the terminal
window open while using the app; press Ctrl+C to stop it.

On any platform with Python 3.11 or newer:

```bash
python scripts/serve_demo.py
```

The browser application needs an internet connection on startup to download
Pyodide, Pydantic and SQLite. CSV processing stays in the browser. Select Customer
or Transaction, load a sample or a UTF-8 CSV up to 5 MB, inspect accepted and
rejected rows, and download the rejects CSV. MySQL is needed only for CLI database
loads and integration checks.

Current application fixes, packaging and verified MySQL staging results: [completion report](docs/completion-report.md).

## Architecture

```mermaid
flowchart LR
    A[CSV / Excel / REST API] --> B[Source + lineage]
    B --> C[Cleaner]
    C --> D[Pydantic v2 + dataset checks]
    D -->|accepted| E[MySQL staging]
    E --> F[Append / replace / upsert / SCD2]
    F --> G[Reconciliation]
    D -->|rejected| H[CSV + JSON rejects]
    G --> I[Atomic audit + batch ledger]
    H --> J[Run summary / failure policy]
    I --> J
    D -. same Python core .-> K[Pyodide browser demo]
    K --> L[In-memory SQLite only]
```

The `etlguard.core` / cleaning / checks / validation modules **do not import SQLAlchemy or MySQL**. Database code lives in `etlguard.loaders` and is imported lazily by the orchestration layer. The demo runs the shared validator in the browser instead of duplicating it in JavaScript.

## Install this v0.1.2 production-hardening candidate

From the directory containing the supplied wheel:

```bash
python -m pip install ./etl_validation_toolkit-0.1.2-py3-none-any.whl
etlguard --help
```

The wheel contains the Python package and bundled sample YAML/CSV/XLSX files.
The source ZIP contains tests, documentation, configuration examples, and the browser demo.
The package is not published on PyPI in this handover.

## Quick start: validate in under a minute

Requirements: **Python 3.11+**. MySQL and Docker are **not needed** for validation-only mode.

```bash
git clone https://github.com/theraghavraman/etl-validation-toolkit.git
cd etl-validation-toolkit
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

# Passing synthetic feed; writes only reports (exit 0)
etlguard validate src/etlguard/examples/pipelines/customers_clean.yaml

# Intentionally dirty synthetic feed; 37.5% rejected > 5% limit (exit 1)
etlguard validate src/etlguard/examples/pipelines/customers.yaml

# A quick profile without a schema or database
etlguard profile src/etlguard/examples/data/customers_messy.csv
```

A YAML pipeline is self-contained; relative file paths are resolved relative to **the YAML file**, not the current shell working directory. Run reports are written under `reports/` (gitignored); each batch gets a unique UUID.

### Example pipeline

```yaml
pipeline: customers_daily
source: {type: csv, path: ../data/customers_messy.csv, chunksize: 1000}
model: etlguard.examples.models:Customer
cleaning:
  trim: true
  null_tokens: ["", NA, N/A, "null", "-"]
  date_columns: [signup_date, end_date]
  number_columns: [credit_limit]
  lowercase: [email, status]
  uppercase: [customer_id]
checks:
  unique: [customer_id]
  max_null_pct: {email: 0.5}
  expected_row_count: {min: 1, max: 100000}
target: {table: customers, mode: upsert, key: [customer_id], batch_size: 500}
policy: {fail_if_reject_rate_above: 0.05}
output: {directory: ../../../../reports}
```

### Example output (synthetic dirty feed)

```text
ETL Guard: customers_daily [failed]
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ Metric             ┃ Value            ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ Rows read          │ 8                │
│ Rows cleaned       │ 8                │
│ Valid              │ 4                │
│ Rejected           │ 3                │
│ Duplicates dropped │ 1                │
│ Loaded             │ 0 (dry run)      │
└────────────────────┴──────────────────┘
Errors by field: __row__: 1, email: 1, signup_date: 1
Failures: reject rate 37.50% exceeds 5.00%
Summary: reports/customers_daily_<batch-id>_summary.json
Rejects: reports/customers_daily_<batch-id>_rejects.csv
```

A committed [sample rejects CSV](docs/sample_rejects.csv) and [sample rejects JSON](docs/sample_rejects.json) show actual Pydantic errors from the synthetic customer feed. Each rejected-row file uses this machine-readable header:

```csv
batch_id,source_row_number,field,error_type,error_message,raw_value,raw_row_json
```

The file writes **one entry per error** (a row can have several errors), so `rows_rejected` is a count of **distinct source records** rather than the number of reject-file lines. Raw JSON preserves the original incoming record, including bad values and original field names. Examples of errors include invalid email format, impossible signup date, and end-date-before-start-date. Rejected data is synthetic in this repository.

## Run with MySQL 8

Use Docker Compose **only if you want the local MySQL example**. You can also point at your own MySQL 8 installation; no container is required in production.

```bash
docker compose up -d mysql
# Windows PowerShell: $env:ETLGUARD_DATABASE_URL = 'mysql+pymysql://etlguard:etlguard_local_only@127.0.0.1:3307/etlguard'
export ETLGUARD_DATABASE_URL='mysql+pymysql://etlguard:etlguard_local_only@127.0.0.1:3307/etlguard'
etlguard init-db
etlguard run src/etlguard/examples/pipelines/customers_clean.yaml
```

The dirty `customers.yaml` is deliberately configured to fail and **does not merge into MySQL** when its reject threshold is breached. The clean sample is the runnable load example. MySQL DDL is prepared before the transaction because MySQL DDL can implicitly commit. Staging inserts, target DML, reconciliation, and staging cleanup share one transaction. `truncate_insert` deliberately uses a transactional `DELETE`, **not** `TRUNCATE` (which implicitly commits in MySQL).

| Mode | Behaviour | Reconciliation semantics |
| --- | --- | --- |
| `append` | Insert all validated rows | every accepted row is inserted |
| `truncate_insert` | Transactionally delete target contents, then insert | every accepted row is inserted |
| `upsert` | Business-key `INSERT … ON DUPLICATE KEY UPDATE` | existing matched keys count as updated (even if values were unchanged) |
| `scd2` | Insert new business keys; close the old current version when tracked values change | unchanged matched keys count as processed/updated, and are separately counted as `rows_unchanged` |

For SCD2, each version has `effective_from` (inclusive), `effective_to` (exclusive; `NULL` for current), and `is_current`. Version boundaries are stored as UTC `DATETIME(6)`; historic records are retained. The loader serializes participating MySQL ETL Guard writers per target with a bounded advisory lock; the database additionally enforces one current SCD2 version per non-null business key. Coordinate any *external* writers as well. For upsert, an existing target must have a **UNIQUE** constraint on the configured business key; missing constraints and additional competing UNIQUE indexes cause an early failure instead of silently merging the wrong record. Existing table schemas and InnoDB storage engines are checked; older SCD2 tables require an explicit, backup-backed schema upgrade. Business key columns cannot be nullable.

In `truncate_insert` mode, an empty source or any rejected records **blocks replacement by default**, even when the general reject-rate policy is permissive. Explicit `allow_empty_replace` and `allow_partial_replace` opt-ins are available but should be used cautiously. Identical invalid rows are always counted as rejects, never deduplicated away.

A run that fails validation writes a failed summary/rejects; if a database URL is available it also attempts a failed audit row. A run with an SQL or reconciliation failure rolls back the DML transaction, writes a failed summary and attempts a failure audit. For successful loads, both the audit and durable commit marker are part of the **same target transaction**; a failed audit before commit rolls back the load. `etlguard init-db` creates the shared tables before jobs run. A separate failure audit is best-effort.

## MySQL 8 staging and shadow-pilot verification

This source archive includes a **fail-closed MySQL 8 staging gate**, an immutable-input
**shadow pilot planner/runner**, and a **human promotion-evidence assessor**. None of
these scripts grants permission to write to a production database. See
[Shadow pilot and promotion](docs/shadow-pilot-and-promotion.md) for the end-to-end
commands, the disposable `etlguard_shadow_ci` schema, and the review checklist.

## Python API

```python
from etlguard import Pipeline, RunResult

pipeline = Pipeline.from_yaml("src/etlguard/examples/pipelines/customers_clean.yaml")
result: RunResult = pipeline.run(dry_run=True)  # Never contacts MySQL
assert result.ok
print(result.rows_read, result.rows_valid, result.rows_rejected)
print(result.reject_csv, result.summary_json)
```

## Validation rules and provenance

- **Input lineage:** every `SourceRecord` carries `source_name`, `source_row_number`, UTC `ingested_at`, and UUID `batch_id`. The staging table also records the batch, source row number and ingest timestamp. CSV uses 1-based logical record row numbers (for simple CSV these equal physical lines); Excel counts worksheet rows; API counts records across pages.
- **Cleaning before validation:** whitespace trim, configurable case-insensitive null tokens, snake_case column names, rename maps, date parsing, Decimal numeric/currency normalization, per-column casing, exact duplicate removal of *accepted* rows (invalid records are not suppressed).
- **Pydantic v2 schemas:** required and optional fields, type coercion, bounds, patterns, enum literals, length limits, and model-level cross-field checks. All independent Pydantic field errors for a rejected record are recorded. Model validators run when their required fields can be parsed.
- **Dataset checks:** uniqueness among accepted candidates, reference-set membership (inline or CSV), expected cleaned row-count range, and column null-percentage limits. Dataset-level threshold failures are recorded in the summary rather than attributed to arbitrary individual rows.
- **Threshold and exit codes:** return `0` for passing validation or successful load, `1` for reject-rate, dataset, source, DB or reconciliation failure, and `2` for invalid pipeline config or missing required run configuration.

Details, full YAML field reference and examples: [Configuration reference](docs/configuration.md).

## Browser-only demo

The static `demo/` site runs Python in [Pyodide](https://pyodide.org/), loads the **same `ValidationEngine`** as the CLI, and inserts accepted records into an **in-memory SQLite** table. Drag in a UTF-8 CSV (up to 5 MB), select `Customer` or `Transaction`, and immediately inspect counts, field-level reasons, accepted rows, and downloadable rejects CSV. Synthetic samples load automatically and contain known failures. **No network requests upload the user’s CSV**; the demo downloads its Python runtime from a public CDN. Database credentials and MySQL are never loaded into the browser.

To preview locally (use HTTP, not `file://`):

```bash
python scripts/build_demo_bundle.py
python -m http.server 8000 --directory demo
# http://localhost:8000
```

GitHub Pages deployment is automated by the CI workflow on pushes to `main` **after** the repository is published and **Settings → Pages → Build and deployment → Source: GitHub Actions** is selected. See [publishing guide](docs/deployment.md).

## Quality gates

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy src/etlguard
pytest -q --cov=etlguard --cov-branch --cov-report=term-missing --cov-report=xml --cov-fail-under=85
# MySQL integration tests activate only when ETLGUARD_TEST_MYSQL_URL is set.
```

The workflow runs lint, types, unit tests, Hypothesis checks, mocked API tests and **real MySQL 8** integration tests including upsert, SCD2, rollback, audit, and reconciliation. CI publishes a coverage badge JSON endpoint with GitHub Pages. Locally, MySQL integration tests are skipped unless their URL is supplied. A green badge is **not claimed** until CI has run.

## Project structure

```text
src/etlguard/
├── sources/                 # CSV chunks, Excel, paginated httpx API, lineage
├── cleaning.py, validation.py, checks.py, core.py
├── rejects.py, profile.py, config.py, pipeline.py, cli.py
├── loaders/                 # MySQL staging and transactional merge
├── scd2.py, audit.py, reconcile.py
├── browser_demo.py          # Same core + SQLite for Pyodide
└── examples/               # Synthetic models, YAML pipelines, CSV + XLSX

tests/                      # Unit, property, mocked API, MySQL integration, CLI
demo/                       # Static GitHub Pages app with core bundle
docs/                       # Configuration, deployment, technical decisions
.github/workflows/          # CI, Pages, release
scripts/                    # Demo bundle and coverage badge generator
```

## Known boundaries

This is a **portfolio-scale validation and load framework**, not a distributed ETL scheduler: validation buffers accepted rows in memory for dataset-wide checks even though CSV reading is chunked. It is suitable for ordinary batch sizes, but very large feeds need spill-to-disk or partitioned validation. API pagination has a `max_pages` safety limit. Table creation is automatic; existing-schema migrations and concurrent SCD2 writers are intentionally out of scope. The demo supports **CSV only** and uses SQLite in place of MySQL. See [design notes](docs/design-decisions.md).

## Licence and release

MIT — see [LICENSE](LICENSE). Changes are recorded in [CHANGELOG.md](CHANGELOG.md). This v0.1.2 production-hardening candidate is delivered as local source and wheel artifacts; the archive excludes Git internals and is not itself a published GitHub release. Tag v0.1.2 only after the complete MySQL 8-backed CI workflow and staging acceptance checks pass. No live repository, Pages URL, or public release is claimed here.

## v0.1.2 staging-to-production controls

A successful ETL load now writes **data + batch ledger + success audit in one InnoDB transaction**. The new `etl_batch_commits` table prevents re-execution of a committed batch UUID. An identical retry is read-only; a changed feed/configuration using the same UUID fails closed. `etlguard batch-status <uuid>` provides the persisted commit state.

The loader serializes MySQL writers per target using an advisory lock; new SCD2 tables also enforce a generated-column `UNIQUE` guard on active rows. For pre-existing SCD2 tables, stop writers, back up the table, then use the operator-confirmed `etlguard upgrade-scd2 pipeline.yaml --yes` command. Full instructions, MySQL 8 tests, retry/rollback procedure and go/no-go acceptance checklist: **[Production hardening runbook](docs/production-v0.1.2.md)**.

```bash
etlguard init-db
etlguard run path/to/pipeline.yaml --batch-id 11111111-1111-4111-8111-111111111111
etlguard batch-status 11111111-1111-4111-8111-111111111111
```

**Operational status:** staged release candidate. A real MySQL 8 staging run and the integration suite are required before production promotion. The MySQL 8 staging matrix and synthetic shadow pilot passed; see the [completion report](docs/completion-report.md) for exact evidence.

## MySQL 8 staging verification (v0.1.2)

Before enabling unattended ETL jobs, follow the [MySQL 8 Staging and Controlled Pilot Gate](docs/mysql8-staging-and-pilot-gate.md). The included `scripts/verify_mysql8_staging.py` refuses production-style database names and requires a confirmed disposable MySQL 8 schema. The local Docker Compose file `docker-compose.mysql8-staging.yml` and manual GitHub Actions workflow are included for testing; successful tests alone do not authorize a production deployment.


### Phase 3: independent shadow row reconciliation (source-only add-on)

After a successful disposable MySQL 8 staging gate and first shadow load,
run `scripts/verify_shadow_reconciliation.py` to compare **actual row values**,
audit, commit ledger and staging cleanup against the pinned source snapshot.
Re-run after the same-UUID retry and compare `target_physical_sha256`: it must
not change. The MySQL CI shadow smoke includes both checks. This verifier
**never writes database rows** and never grants production deployment approval.
See `docs/phase-3-shadow-reconciliation.md` for the exact commands and evidence
requirements. The installable wheel/runtime remain at v0.1.2.
