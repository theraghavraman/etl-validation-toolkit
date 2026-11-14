# ETLGuard

**Bad rows in a CSV, Excel or API feed reach the database unnoticed; ETLGuard stops them first and tells you which field failed and why.**

## Architecture

```mermaid
flowchart LR
    A[CSV / Excel / REST API] --> B[Source records + lineage]
    B --> C[Cleaning]
    C --> D[Pydantic + dataset checks]
    D -->|accepted| E[MySQL staging]
    E --> F[Append / replace / upsert / SCD2]
    F --> G[Count reconciliation]
    G --> H[Atomic audit + batch ledger]
    D -->|rejected| I[CSV + JSON rejects]
    H --> J[Run summary]
    I --> J
    D -. shared engine .-> K[Browser: Pyodide + SQLite]
```

## Quickstart

No install: open the [live demo](https://theraghavraman.github.io/etl-validation-toolkit/) and load the sample feed.

From source, with Python 3.11 or newer. Validation needs no database:

```bash
git clone https://github.com/theraghavraman/etl-validation-toolkit.git
```

```bash
python -m pip install ./etl-validation-toolkit
```

```bash
etlguard validate etl-validation-toolkit/src/etlguard/examples/pipelines/customers_clean.yaml
```

That prints a run summary and writes a JSON summary and a rejects report to `etl-validation-toolkit/reports/`. Swap in `customers.yaml` to see a dirty feed fail with exit code 1. To load into MySQL, see [Load into MySQL](#load-into-mysql).

---

[![CI](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Ftheraghavraman.github.io%2Fetl-validation-toolkit%2Fcoverage.json)](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)
[![MIT License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

[**Try the live demo →**](https://theraghavraman.github.io/etl-validation-toolkit/) · [User guide](docs/USER_GUIDE.md) · [Downloads](https://github.com/theraghavraman/etl-validation-toolkit/releases/latest) · [Configuration reference](docs/configuration.md)

ETLGuard is a Python toolkit for cleaning CSV, Excel and REST API feeds, validating records with Pydantic, and loading accepted rows into MySQL. Each run produces a summary and rejected records with the exact field and reason. A YAML pipeline defines the data contract, cleaning rules, quality thresholds and destination.

It brings familiar ETL controls—business keys, rejected rows, source/target reconciliation and SCD Type 2 history—into a reusable Python package. All included datasets are synthetic.

![ETLGuard interactive data quality lab](docs/images/demo.png)

## What it does

- **Read:** CSV, Excel and JSON REST APIs with pagination and retries.
- **Clean:** whitespace, column names, null tokens, dates, numbers, case and accepted duplicates.
- **Validate:** typed Pydantic models, cross-field rules, business-key uniqueness, reference sets, row counts and null thresholds.
- **Explain rejects:** CSV/JSON reports retain the source row, original data, field and error reason. Multiple errors on one record still count as one rejected row.
- **Load MySQL:** append, transactional replacement, upsert and SCD Type 2 history through staging tables.
- **Reconcile:** rollback on a source/target count mismatch. Commit target changes, success audit and batch ledger in the same transaction.
- **Retry safely:** a repeated batch UUID with the same request returns the committed result; changed input or configuration using that UUID is refused.
- **Try in a browser:** the same validation engine runs in Pyodide, with an in-memory SQLite destination.

## Try it without installing Python

Open the [live demo](https://theraghavraman.github.io/etl-validation-toolkit/). Wait for the Python runtime to load, select Customer or Transaction, then load the sample or drop a UTF-8 CSV up to 5 MB. Inspect accepted/rejected rows and download accepted CSV, rejected CSV or summary JSON. Download a schema template to prepare your own feed. If runtime startup fails, click **Retry loading Python**.

CSV contents are processed inside the browser. Startup downloads Pyodide and its libraries from a CDN. The demo uses SQLite in memory; MySQL connections and API ingestion are available through the Python package.

<details>
<summary>Run the browser demo locally</summary>

```bash
git clone https://github.com/theraghavraman/etl-validation-toolkit.git
cd etl-validation-toolkit
python3 scripts/serve_demo.py
```

Source downloads also include `Launch ETL Guard.command` for macOS, `Launch ETL Guard.cmd` for Windows and `Launch ETL Guard.sh` for Linux. The launcher chooses an available local port. Keep its terminal open while using the app; press Ctrl+C to stop it.
</details>

## Install and validate

Requires Python 3.11 or newer. Validation works without a database. Read the [user guide](docs/USER_GUIDE.md) for a complete walkthrough.

After installing a release wheel, create a working example with:

```bash
etlguard --version
etlguard init my-pipeline
etlguard validate my-pipeline/pipeline.yaml
```

```bash
git clone https://github.com/theraghavraman/etl-validation-toolkit.git
cd etl-validation-toolkit
python3 -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

# Passing feed: exit 0; writes JSON summary and reject reports
etlguard validate src/etlguard/examples/pipelines/customers_clean.yaml

# Dirty feed: intentionally exits 1 because its reject rate exceeds the threshold
etlguard validate src/etlguard/examples/pipelines/customers.yaml

etlguard profile src/etlguard/examples/data/customers_messy.csv
```

You can also install the wheel from [Releases](https://github.com/theraghavraman/etl-validation-toolkit/releases). This project is not currently published on PyPI.

### Example result

The bundled messy customer feed gives:

| Rows read | Accepted | Rejected | Duplicates dropped | MySQL writes during validation |
|---:|---:|---:|---:|---:|
| 8 | 4 | 3 | 1 | 0 |

The rejected records contain an invalid email, an invalid date and an invalid date interval. The configured 5% rejection limit fails this batch before loading. See the [sample rejects CSV](docs/sample_rejects.csv) and [JSON](docs/sample_rejects.json).

### Configure a pipeline

```yaml
pipeline: customers_daily
source:
  type: csv
  path: customers.csv
model: etlguard.examples.models:Customer
cleaning:
  trim: true
  date_columns: [signup_date, end_date]
  number_columns: [credit_limit]
  lowercase: [email, status]
  uppercase: [customer_id]
checks:
  unique: [customer_id]
  max_null_pct: {email: 0.1}
  expected_row_count: {min: 1, max: 100000}
target:
  table: customers
  mode: upsert
  key: [customer_id]
policy:
  fail_if_reject_rate_above: 0.05
output:
  directory: reports
```

Paths are relative to the YAML file. Define your own Pydantic `BaseModel` and use `your_package.models:YourModel` for a custom contract. See the [complete configuration reference](docs/configuration.md).

## Load into MySQL

The local example uses MySQL 8 in Docker. Existing MySQL 8 installations work too.

```bash
docker compose up -d --wait mysql
export ETLGUARD_DATABASE_URL='mysql+pymysql://etlguard:etlguard_local_only@127.0.0.1:3307/etlguard'
etlguard init-db
etlguard run src/etlguard/examples/pipelines/customers_clean.yaml
```

| Mode | Behavior |
|---|---|
| `append` | Insert every accepted row. |
| `truncate_insert` | Replace using transactional DELETE + INSERT; empty or partially rejected feeds are blocked by default. |
| `upsert` | Insert new business keys and update matching keys. |
| `scd2` | Keep history using `effective_from`, `effective_to` and `is_current`. |

For a retryable scheduled batch, supply a stable UUID:

```bash
etlguard run path/to/pipeline.yaml --batch-id 11111111-1111-4111-8111-111111111111
etlguard batch-status 11111111-1111-4111-8111-111111111111
```

MySQL tables must use InnoDB. Writers to a target are serialized with an advisory lock; SCD2 also has a unique active-row constraint. Since version 0.1.3, ETLGuard uses READ COMMITTED on load connections to avoid metadata gap-lock deadlocks between different targets. Existing table migrations and writes from other systems require coordination.

## Architecture notes

The diagram is at the [top of this page](#architecture).

The validation core has no MySQL imports. Database loading is imported only when needed. The browser bundles the shared Python modules and calls the same engine used by the CLI.

## Python API

```python
from etlguard import Pipeline

result = Pipeline.from_yaml(
    "src/etlguard/examples/pipelines/customers_clean.yaml"
).run(dry_run=True)
assert result.ok
print(result.rows_valid, result.rows_rejected, result.summary_json)
```

## Development and verification

The commit history discloses AI assistance. Tests and coverage document behavior under the stated conditions; they do not establish who wrote each line. For a code review, start with the [pipeline](src/etlguard/pipeline.py), [MySQL loader](src/etlguard/loaders/mysql.py), and [design decisions](docs/design-decisions.md).

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy src/etlguard
python -m unittest discover -s scripts -p 'test_*.py'

# Against a disposable MySQL database:
export ETLGUARD_TEST_MYSQL_URL="$ETLGUARD_DATABASE_URL"
pytest -q --cov=etlguard --cov-branch --cov-report=term-missing --cov-fail-under=85
python scripts/build_demo_bundle.py
python -m build
```

Without `ETLGUARD_TEST_MYSQL_URL`, local MySQL integration tests are skipped. GitHub CI provisions MySQL and runs them. The [staging workflow](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/mysql8-staging-gate.yml) also checks MySQL 8.0/8.4 with Python 3.11/3.13, including an isolated shadow load, identical retry and independent row reconciliation.

For browser checks: `python -m pip install -e ".[browser]"`, `python -m playwright install chromium`, then `python scripts/verify_browser.py`.

Read [the release verification report](docs/verification-v0.1.4.md) for the checks performed on this release.

## Documentation

- [User guide: installation, schemas, browser exports, CLI and MySQL](docs/USER_GUIDE.md)
- [Product scope and acceptance](docs/product-completion.md)
- [Full interactive engineering and operations manual](docs/ETLGuard-v0.1.3-Manual.pdf)
- [Configuration reference](docs/configuration.md)
- [Deployment and release](docs/deployment.md)
- [Design decisions](docs/design-decisions.md)
- [MySQL staging and controlled pilot](docs/mysql8-staging-and-pilot-gate.md)
- [Independent shadow reconciliation](docs/phase-3-shadow-reconciliation.md)

## Scope and limits

CSV reading is streamed, but accepted records and dataset checks are held in memory. This version suits ordinary batch jobs; large feeds need a measured memory budget. The browser handles CSV only, displays up to 250 rows per view, and keeps its SQLite data only for the current run.

Public release and successful synthetic tests establish the demonstrated behavior. The v0.1.3 engineering manual remains a reference for database operations; the current user guide covers v0.1.4. A business database deployment needs target-specific load, recovery and performance checks. Earlier v0.1.2 reports are historical evidence.

Developed by [Raghav Raman](https://github.com/theraghavraman). MIT license; see [LICENSE](LICENSE) and [CHANGELOG](CHANGELOG.md).
