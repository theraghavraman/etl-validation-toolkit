# ETLGuard

**Bad rows in a CSV, Excel or API feed reach the database unnoticed; ETLGuard stops them first and tells you which field failed and why.**

[![CI](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Ftheraghavraman.github.io%2Fetl-validation-toolkit%2Fcoverage.json)](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)
[![MIT License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

[**Try the live demo →**](https://theraghavraman.github.io/etl-validation-toolkit/) · [User guide](docs/USER_GUIDE.md) · [Downloads](https://github.com/theraghavraman/etl-validation-toolkit/releases/latest) · [Configuration reference](docs/configuration.md)

ETLGuard is a Python toolkit for cleaning CSV, Excel and REST API feeds, validating records with Pydantic, and loading accepted rows into MySQL. Each run produces a summary and rejected records with the exact field and reason. A YAML pipeline defines the data contract, cleaning rules, quality thresholds and destination.

It brings familiar ETL controls—business keys, rejected rows, source/target reconciliation and SCD Type 2 history—into a reusable Python package. All included datasets are synthetic.

![ETLGuard interactive data quality lab](docs/images/demo.png)

## Quickstart

No install: open the [live demo](https://theraghavraman.github.io/etl-validation-toolkit/), wait for the Python runtime to load, select Customer or Transaction, then load the sample or drop a UTF-8 CSV up to 5 MB.

From source, with Python 3.11 or newer. Validation needs no database:

```bash
git clone https://github.com/theraghavraman/etl-validation-toolkit.git
cd etl-validation-toolkit
python -m pip install .

# Passing feed: exit 0; writes a JSON summary and reject reports to reports/
etlguard validate src/etlguard/examples/pipelines/customers_clean.yaml

# Dirty feed: exits 1 because its reject rate is above the threshold
etlguard validate src/etlguard/examples/pipelines/customers.yaml

etlguard profile src/etlguard/examples/data/customers_messy.csv
```

To start your own pipeline from an installed package, run `etlguard init my-pipeline`, then `etlguard validate my-pipeline/pipeline.yaml`. The wheel is on the [Releases](https://github.com/theraghavraman/etl-validation-toolkit/releases) page; this project is not on PyPI. To load into MySQL, see [Load into MySQL](#load-into-mysql).

### Example result

The bundled messy customer feed gives:

| Rows read | Accepted | Rejected | Duplicates dropped | MySQL writes during validation |
|---:|---:|---:|---:|---:|
| 8 | 4 | 3 | 1 | 0 |

The rejected records contain an invalid email, an invalid date and an invalid date interval. The configured 5% rejection limit fails this batch before loading. See the [sample rejects CSV](docs/sample_rejects.csv) and [JSON](docs/sample_rejects.json).

## What it does

- **Read:** CSV, Excel and JSON REST APIs with pagination and retries.
- **Clean:** whitespace, column names, null tokens, dates, numbers, case and accepted duplicates.
- **Validate:** typed Pydantic models, cross-field rules, business-key uniqueness, reference sets, row counts and null thresholds.
- **Explain rejects:** CSV/JSON reports retain the source row, original data, field and error reason. Multiple errors on one record still count as one rejected row.
- **Load MySQL:** append, transactional replacement, upsert and SCD Type 2 history through staging tables.
- **Reconcile:** rollback on a source/target count mismatch. Commit target changes, success audit and batch ledger in the same transaction.
- **Retry safely:** a repeated batch UUID with the same request returns the committed result; changed input or configuration using that UUID is refused.
- **Try in a browser:** the same validation engine runs in Pyodide, with an in-memory SQLite destination.

## When to use something else

ETLGuard validates a file or API feed and loads it into MySQL in the same run. If you only need one of those halves, or a different place to run checks, these tools are more established.

| If you need | Use | Why |
| --- | --- | --- |
| Checks on data already in a warehouse, Spark or a database, with generated data docs | [Great Expectations](https://greatexpectations.io) | It runs against many backends and has a large library of expectations; ETLGuard checks a feed before it is loaded |
| Schema checks on pandas or Polars DataFrames inside your own code | [Pandera](https://pandera.readthedocs.io) | It validates DataFrames in place, including statistical checks; ETLGuard validates record by record with Pydantic |
| Tests on models you build in the warehouse | [dbt data tests](https://docs.getdbt.com/docs/build/data-tests) | They run where the transformation runs |
| Loading many sources without writing a model per feed | [dlt](https://dlthub.com) or [Airbyte](https://airbyte.com) | They ship connectors and infer schemas; ETLGuard needs a Pydantic contract per feed |

ETLGuard fits when a feed arrives as a file or API response, every rejected row needs a reason someone can act on, and the load into MySQL (with reconciliation, retries and SCD2 history) should happen only if the batch passes.

## Configure a pipeline

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
docker compose -f docker/compose.yml up -d --wait mysql
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

MySQL tables must use InnoDB. Writers to a target are serialized with an advisory lock; SCD2 also has a unique active-row constraint. Load connections use READ COMMITTED to avoid metadata gap-lock deadlocks between different targets. Existing table migrations and writes from other systems require coordination.

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

## Run the browser demo locally

CSV contents are processed inside the browser. Startup downloads Pyodide and its libraries from a CDN. The demo uses SQLite in memory; MySQL connections and API ingestion are available through the Python package. If runtime startup fails, click **Retry loading Python**.

```bash
python3 scripts/serve_demo.py
```

Source downloads also include launchers in `launchers/`: `etlguard-demo.command` for macOS, `etlguard-demo.cmd` for Windows and `etlguard-demo.sh` for Linux. Each one picks a free local port. Keep its terminal open while using the app; press Ctrl+C to stop it.

## Development and verification

For a code review, start with the [pipeline](src/etlguard/pipeline.py), [MySQL loader](src/etlguard/loaders/mysql.py), and [design decisions](docs/design-decisions.md).

```bash
python3 -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
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

Without `ETLGUARD_TEST_MYSQL_URL`, local MySQL integration tests are skipped. GitHub CI provisions MySQL and runs them. A [second workflow](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/mysql8-staging-gate.yml) runs them on MySQL 8.0 and 8.4 with Python 3.11 and 3.13, loads a copy into a separate schema, repeats the load with the same batch ID, and checks the row counts with an independent query.

For browser checks: `python -m pip install -e ".[browser]"`, `python -m playwright install chromium`, then `python scripts/verify_browser.py`.

The [release verification report](docs/verification-v0.1.4.md) lists the checks run for the current release.

## Documentation

- [User guide: installation, schemas, browser exports, CLI and MySQL](docs/USER_GUIDE.md)
- [Configuration reference](docs/configuration.md)
- [Product scope](docs/product-completion.md)
- [Design decisions](docs/design-decisions.md)
- [Deployment and release](docs/deployment.md)
- [MySQL 8 test matrix](docs/mysql8-staging-and-pilot-gate.md)
- [Shadow-schema reconciliation check](docs/phase-3-shadow-reconciliation.md)
- [Archive](docs/archive/): reviews and runbooks from v0.1.1–v0.1.3, including the v0.1.3 PDF manual

## Scope and limits

CSV reading is streamed, but accepted records and dataset checks are held in memory. This version suits ordinary batch jobs; large feeds need a measured memory budget. The browser handles CSV only, displays up to 250 rows per view, and keeps its SQLite data only for the current run.

The tests use synthetic data. Before pointing ETLGuard at a business database, test load time, recovery and performance against that database.

Developed by [Raghav Raman](https://github.com/theraghavraman). MIT license; see [LICENSE](LICENSE) and [CHANGELOG](CHANGELOG.md).
