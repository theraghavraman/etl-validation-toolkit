# ETLGuard user guide

ETLGuard cleans and validates tabular feeds, explains invalid records and loads accepted data into MySQL through transactional staging tables.

## Start in the browser

Open [ETLGuard](https://theraghavraman.github.io/etl-validation-toolkit/). The first visit downloads Python and its validation libraries. If startup fails, use **Retry loading Python**.

1. Choose the Customer or Transaction schema.
2. Load the synthetic sample, or download a schema template and fill it with your own rows.
3. Drop a UTF-8 CSV up to 5 MB.
4. Inspect accepted records, rejected records and the batch summary.
5. Download accepted rows, rejected rows or the summary JSON.

Accepted rows are also inserted into a temporary SQLite table inside the browser. CSV data is not uploaded. The table is discarded after the current run. Database loads, Excel ingestion, API ingestion and custom Python models use the Python package.

## Install the Python package

Download the wheel from [Releases](https://github.com/theraghavraman/etl-validation-toolkit/releases/latest). Python 3.11 or newer is required.

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1
python -m pip install path/to/etl_validation_toolkit-0.1.4-py3-none-any.whl
etlguard --version
etlguard init my-pipeline
etlguard validate my-pipeline/pipeline.yaml
```

`init` creates working CSV/XLSX examples, a pipeline YAML file and a local reports path. It refuses to overwrite an existing directory. Commands also work as `python -m etlguard`.

For the source distribution, install with `python -m pip install -e ".[dev]"`.

## Understand the example schemas

**Customer** requires `customer_id`, `full_name`, `signup_date`, `status` and `credit_limit`. Optional fields are `email` and `end_date`. IDs look like C001. Status is active or inactive. Credit limit is nonnegative. An end date must not precede signup.

**Transaction** requires `transaction_id`, `customer_id`, `transaction_date`, `type` and `amount`. Description is optional. IDs look like T001 and C001. Type is debit or credit. Debits must have a positive amount.

Use ISO dates such as `2026-11-12` to avoid ambiguity. The YAML cleaning rules can recognize additional date formats and normalize whitespace, null tokens, currency strings and case.

CSV headers must be nonempty and unique. The CLI refuses records whose cell counts differ from the header, including files that a permissive parser might silently shift. Excel duplicate and empty column headers are also refused. A failed source read prevents the entire batch from loading.

## Read run results

- **Read:** all source records encountered.
- **Valid:** accepted records after validation and valid-row deduplication.
- **Rejected:** distinct invalid source records.
- **Duplicates dropped:** copies of previously accepted records.
- **Loaded:** accepted records processed by the destination; zero during validation.

The invariant is `read = valid + rejected + duplicates dropped`. One rejected record may produce several error lines in the rejects CSV/JSON. CSV exports escape spreadsheet formulas; JSON preserves exact source values.

`policy.fail_if_reject_rate_above` is a fraction. `0.05` means 5%. Dataset thresholds such as row counts and null percentages can also fail a batch. A failed quality gate prevents loading.

CLI exit codes are 0 for success, 1 for a failed source/quality/database run, and 2 for a configuration error.

## Configure Excel or a REST feed

Edit `my-pipeline/pipeline.yaml` and keep the model and cleaning rules appropriate to your feed.

```yaml
source:
  type: excel
  path: data/customers_messy.xlsx
  sheet: 0
  header_row: 1
```

For a JSON API, replace the URL with your actual endpoint:

```yaml
source:
  type: api
  url: https://api.example.com/customers
  records_path: data.items
  bearer_token_env: CUSTOMER_API_TOKEN
  pagination:
    mode: page
    start: 1
    page_size: 100
    max_pages: 100
```

Authentication values come from environment variables. The API source performs GET requests, retries transient failures and refuses to forward credentials to a next-page link on a different origin. See [all configuration options](configuration.md).

## Use your own validation model

Define a Pydantic v2 model in an importable module, then set `model: my_package.models:MyModel`. Dataset-check column names must exist on that model; misspellings fail as configuration errors.

```python
from decimal import Decimal
from pydantic import BaseModel, Field

class Price(BaseModel):
    product_id: str = Field(max_length=40)
    price: Decimal = Field(max_digits=12, decimal_places=4)
```

For MySQL, Decimal constraints set the database precision and scale. Unconstrained Decimal fields default to DECIMAL(18,2). Values that do not fit either a new or existing DECIMAL column are refused before loading, preventing silent rounding. Complex nested model fields require an explicit tabular mapping before MySQL loading.

## Load into MySQL

Start the supplied local MySQL example from the source distribution:

```bash
docker compose up -d --wait mysql
export ETLGUARD_DATABASE_URL='mysql+pymysql://etlguard:etlguard_local_only@127.0.0.1:3307/etlguard'
etlguard init-db
etlguard run my-pipeline/pipeline.yaml
```

On PowerShell, set the URL using `$env:ETLGUARD_DATABASE_URL = '...'`. Use a separate database/schema when running integration tests.

Load modes:

| Mode | Result |
|---|---|
| append | Insert all accepted rows. |
| truncate_insert | Transactional DELETE then INSERT. Empty or partially rejected replacements are blocked unless explicitly opted in. |
| upsert | Insert new keys, update matching business keys. |
| scd2 | Close changed current versions and insert new versions, retaining history. |

SCD2 uses `effective_from`, `effective_to` and `is_current`. Specify `target.key` and `target.tracked_columns`. Existing target schemas must satisfy the documented constraints. Audit, commit ledger, staging cleanup and target DML share an InnoDB transaction.

## Retry a scheduled batch

Use a stable batch UUID when rerunning the same immutable input and configuration:

```bash
etlguard run my-pipeline/pipeline.yaml --batch-id 11111111-1111-4111-8111-111111111111
etlguard batch-status 11111111-1111-4111-8111-111111111111
```

An identical committed retry returns recorded counts without loading again. Changed input/configuration with that UUID is refused. Use a new UUID for a different batch. Uncertain database outcomes should be resolved with batch-status before retrying or compensating.

## Run the browser locally

Extract the portable source ZIP, then use the launcher for your platform:

- macOS: `Launch ETL Guard.command`
- Windows: `Launch ETL Guard.cmd`
- Linux: `sh 'Launch ETL Guard.sh'`

Or run `python scripts/serve_demo.py`. The source ZIP includes the browser bundle. After changing Python validation code, rebuild it with `python scripts/build_demo_bundle.py`.

## Troubleshooting

| Situation | Action |
|---|---|
| Browser cannot load Python | Check internet/CDN access, then click Retry loading Python. |
| CSV is rejected before validation | Check UTF-8 encoding, unique headers, quotes and consistent field counts. |
| Wrong schema | Choose the matching schema or use a custom Pydantic model through the CLI. |
| Dirty sample exits 1 | Expected: the sample intentionally exceeds its configured reject threshold. |
| Dataset check references an unknown field | Correct the YAML column name. |
| Decimal does not fit target | Declare the required precision/scale and explicitly migrate the existing target schema. |
| Database URL missing | Set the environment variable named by database_url_env. |
| Batch already committed | Identical retry succeeded without new writes; inspect batch-status. |
| Tests skip MySQL | Set ETLGUARD_TEST_MYSQL_URL to a disposable MySQL 8 schema. |

Validation retains accepted records and global-check data in memory. Very large feeds need a measured memory budget. This package does not provide a distributed scheduler or automatic database schema migration.
