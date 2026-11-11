# Configuration reference

Each YAML pipeline is independently validated using strict Pydantic v2 configuration models. Unknown keys, missing required source locations and invalid SQL identifiers produce a configuration error (CLI exit code `2`). Relative paths are resolved from the YAML file directory.

## Source

| YAML property | Meaning | Default |
| --- | --- | --- |
| `source.type` | `csv`, `excel`, `api` | required |
| `source.name` | Optional lineage/display name | file name or URL |
| `source.path` | CSV or Excel file | required for file sources |
| `source.delimiter`, `encoding` | CSV separators and codec | `,`, `utf-8-sig` (accepts BOM) |
| `source.header_row` | 1-based header row; CSV counts header lines, Excel relative to `skip_rows` | `1` |
| `source.chunksize` | pandas CSV input rows per chunk | `10000` |
| `source.sheet` | Excel sheet name or 0-based index | `0` |
| `source.skip_rows` | Number of leading worksheet rows to skip | `0` |
| `source.url`, `records_path` | REST GET URL and nested list location, e.g. `data.items` | URL required for API |
| `source.bearer_token_env` | Name of an environment variable containing a bearer token | none |
| `source.timeout_seconds` | httpx per-request timeout | `15` |
| `source.max_retries`, `backoff_seconds` | Exponential backoff on transient network/429/5xx errors | `3`, `0.5` |
| `source.pagination` | See below | `{mode: none}` |

### Page/offset pagination

```yaml
source:
  type: api
  name: synthetic_transactions
  url: https://api.example.test/v1/transactions
  records_path: data.items
  bearer_token_env: DATA_FEED_API_TOKEN
  max_retries: 3
  backoff_seconds: 0.5
  pagination:
    mode: page                 # or offset
    page_param: page
    offset_param: offset
    limit_param: limit
    page_size: 100
    start: 1                  # normally 0 for offsets
    max_pages: 100
```

Page and offset pagination stop when a response contains fewer records than `page_size`. Next-link mode reads `links.next` by default; links must retain the same URL origin as the initial API to prevent bearer tokens from being forwarded to a different server. Repeated next links are rejected. `max_pages` limits accidental endless feeds. Endpoint and token values in this documentation are placeholders, not live services or valid credentials.

```yaml
source:
  type: api
  url: https://api.example.test/v1/customers
  records_path: data.items
  pagination: {mode: next, next_path: links.next, max_pages: 100}
```

## Cleaning

```yaml
cleaning:
  trim: true
  snake_case_columns: true
  rename_map: {"Client Number": customer_id}
  null_tokens: ["", "NA", "N/A", "null", "-"]
  date_columns: [signup_date, end_date]
  date_formats: ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"]
  number_columns: [credit_limit]
  lowercase: [email, status]
  uppercase: [customer_id]
  drop_duplicates: true
```

Cleaning is deterministic and idempotent: applying the same configuration again should not change the data. Number parsing removes common `$`, `€`, `£`, `₹`, comma and spacing separators, parses accounting negatives like `(1,234.00)`, and preserves `Decimal`. Unparseable values are passed to Pydantic to produce a reject rather than silently erased. Date parsing honors the format list order (ambiguous `01/02/2026` is intentionally interpreted according to your configuration). The duplicate fingerprint is the complete normalized row, and duplicates are suppressed only after both row-level and reference/uniqueness checks pass; repeated business keys with differing data are **not** silently dropped and are caught by `checks.unique`.

## Pydantic model and dataset checks

```yaml
model: etlguard.examples.models:Customer
checks:
  unique: [customer_id]
  foreign_keys:
    customer_id: {values: [C001, C002, C003]}
    # Or: {path: ../data/reference_customers.csv, column: customer_id}
  expected_row_count: {min: 1, max: 50000}
  max_null_pct: {email: 0.10}
```

`unique` checks accepted candidate rows; a second record with the same business key is rejected. `foreign_keys` checks records against inline reference values or an independent CSV reference list, not against live database tables. `expected_row_count` and `max_null_pct` run over the cleaned (pre-validation) rows and put dataset failures in the run summary. A schema field may be optional, yet a dataset null-threshold can still fail the batch.

Models live in importable Python modules with `BaseModel`, `Field` constraints and optional `@model_validator(mode="after")` methods. See [`Customer` and `Transaction`](../src/etlguard/examples/models.py). The model import string is `dotted.python.module:ClassName`, and the package containing a custom model must be installed or on `PYTHONPATH`.

## Target and policy

```yaml
target:
  table: customers
  mode: upsert          # append | truncate_insert | upsert | scd2
  key: [customer_id]   # required for upsert or scd2
  tracked_columns: [full_name, email, status]  # for scd2; defaults to all non-key fields
  batch_size: 500
  lock_timeout_seconds: 30
  min_replace_ratio: null  # e.g. 0.90 for truncate_insert minimum-vs-current count
policy:
  fail_if_reject_rate_above: 0.05
  allow_partial_replace: false  # recommended; applies to truncate_insert only
  allow_empty_replace: false    # recommended; applies to truncate_insert only
output:
  directory: ../../../../reports
database_url_env: ETLGUARD_DATABASE_URL
```

**Important:** Use only a local or trusted MySQL database; a real run creates missing tables and writes to `stg_<table>`, the target table and `etl_run_audit`. Business-key SQL identifiers are validated. A SCD2 table stores historical versions. No auto-migration is performed for pre-existing tables. The configured failure policy prevents the load when rejects exceed the threshold; dataset-level failures also prevent loading. For `truncate_insert`, the toolkit *additionally* refuses any rejected source records or a genuinely empty source by default because replacement can erase existing data. Set `policy.allow_partial_replace: true` or `policy.allow_empty_replace: true` **only after explicitly confirming that loss of target rows is acceptable**. Identical invalid rows are never silently de-duplicated; each counts toward quality thresholds. Pipeline IDs must be filename-safe identifiers (1–128 characters).

## CLI and Python API

```bash
etlguard validate path/to/pipeline.yaml # reports only, no database access
etlguard run path/to/pipeline.yaml      # validate + stage + merge + reconcile + audit
etlguard profile path/to/feed.csv       # column overview
etlguard init-db                        # create audit + atomic-commit ledger (requires URL env)
```

`0` = success; `1` = quality/job/load error; `2` = configuration error. Both dry runs and load runs write a `<pipeline>_<uuid>_summary.json`, `_rejects.csv` and `_rejects.json` in the output directory. The CLI produces a Rich table; `Pipeline.from_yaml(...).run(dry_run=True)` returns a Pydantic `RunResult`.

## v0.1.2 concurrency, audit and retries

- Both `etl_run_audit` and `etl_batch_commits` are required; `etlguard init-db` creates them outside the load transaction. Data, a success audit and its batch ledger entry commit together on InnoDB.
- Business key columns for `upsert` and `scd2` **must be NOT NULL**. `scd2` targets require a generated active-row uniqueness guard; migrate existing tables explicitly using `etlguard upgrade-scd2 <pipeline.yaml> --yes` in a maintenance window.
- For recoverable runs, assign and persist a batch UUID before the first attempt and use `etlguard run <config.yaml> --batch-id <uuid>`. After interruption, call `etlguard batch-status <uuid>` and retry with the same UUID and immutable source/config. Different input with the same UUID is rejected.
- `target.lock_timeout_seconds` (0–300; default 30) sets the MySQL advisory lock wait limit. `target.min_replace_ratio` (0–1, optional) protects against drastic shrinkage during `truncate_insert`. All four ETL tables must use InnoDB.

See [the production-hardening runbook](production-v0.1.2.md) for migration and staging test commands.
