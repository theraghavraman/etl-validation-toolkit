# v0.1.2 production-hardening runbook (MySQL 8)

**Release status:** staging candidate until the real MySQL 8 integration suite, schema checks, and a staging load have passed in your environment. Local SQLite tests do not validate MySQL transaction/advisory-lock/index behavior.

## Safeguards added

1. **Atomic commit:** For each successful loader call, target rows, `etl_batch_commits` (SHA-256 request fingerprint and logical row counts), and `etl_run_audit` success status are written on the same InnoDB transaction. Before doing DML, the code verifies that the target, staging, audit, and ledger tables all use InnoDB. For a direct `MysqlLoader.load()` call, an audit is also written with pipeline/source `direct_api`.
2. **Idempotent retries:** A canonical UUID batch ID is the retry identity; the complete incoming record stream plus validated pipeline settings and model schema form a deterministic request hash. Reusing the same UUID with the same input/configuration returns the committed logical counts without touching target rows. Using the UUID with changed input/settings is rejected. The low-level `load` method computes its own fingerprint when no `request_hash` is passed.
3. **Unknown commit recovery:** If an error occurs when a commit acknowledgement is lost, the loader checks the durable ledger. Only a matching committed record can prove success. If the database cannot be contacted, the commit outcome is **unknown**, not proven rolled back; query status after restoration and retry the **same UUID**, not a fresh UUID.
4. **Concurrent SCD2:** Every MySQL ETL Guard writer obtains a server-side `GET_LOCK('etlguard:table:<table>', timeout)` for the target before merging. New SCD2 tables additionally have a generated `__etl_current_marker` and a unique index on `(business_key_columns..., __etl_current_marker)`; the marker is `1` for current rows and `NULL` for historical rows. The database thus rejects a second active version of a business key even when external code bypasses the ETL advisory lock. The business key must be `NOT NULL`.
5. **Safer replacement:** `truncate_insert` continues to use **transactional DELETE, never MySQL TRUNCATE**. Direct loader calls refuse an empty input without `allow_empty_replace=True`; pipeline YAML also blocks empty and partially rejected replacements by default. Set `target.min_replace_ratio` (e.g. `0.90`) to reject sudden drops relative to the existing target row count **before** deletion. All input records are staged before replacing the target.
6. **Operational visibility:** `etlguard batch-status <uuid>` reads committed evidence; failures are audited separately and cannot overwrite an existing successful audit. JSON summaries disclose `request_hash`, `durable_commit`, `replayed`, and `warnings`. Report file failures after a committed transaction are warnings, not load failures. Source stream failures retain partial counters and block loading. Rejected CSV values beginning with spreadsheet formula characters are escaped; JSON retains the exact original value.

## Fresh installation and staging verification

Requires Python 3.11+, a disposable **MySQL 8.x** InnoDB database and database credentials for creating tables/indexes during staging (use separate minimum-privilege credentials in production).

```bash
python -m pip install ./etl_validation_toolkit-0.1.2-py3-none-any.whl
etlguard --help

# Optional local MySQL 8 test database provided by the source ZIP.
docker compose up -d mysql
export ETLGUARD_TEST_MYSQL_URL='mysql+pymysql://etlguard:etlguard_local_only@127.0.0.1:3307/etlguard'
export ETLGUARD_DATABASE_URL="$ETLGUARD_TEST_MYSQL_URL"

# Initialize shared audit and commit tables outside the target transaction.
etlguard init-db

# From the extracted SOURCE ZIP, with development dependencies installed:
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_mysql_integration.py -m integration
python -m pytest -q --cov=etlguard --cov-branch --cov-fail-under=85
ruff check .
mypy src/etlguard

# Validate without touching MySQL first.
etlguard validate src/etlguard/examples/pipelines/customers_clean.yaml
```

Do **not** run tests against real customer data or tables. The test suite creates and drops temporary `it_*` tables, and writes test audit/ledger rows. Always use a disposable test schema.

## Using stable batch IDs in production

The scheduler must generate a unique UUID **before** starting the ETL attempt and persist the assignment alongside the immutable source snapshot. Reuse the **same** UUID and the **same** source/configuration for all retries of that logical attempt; use a different UUID for the next scheduled feed.

```bash
# Generate and retain this ID in your orchestrator/state database.
python -c 'import uuid; print(uuid.uuid4())'

etlguard run path/to/pipeline.yaml --batch-id 11111111-1111-4111-8111-111111111111
etlguard batch-status 11111111-1111-4111-8111-111111111111

# Retrying with the same UUID is a read-only replay if already committed:
etlguard run path/to/pipeline.yaml --batch-id 11111111-1111-4111-8111-111111111111
```

`batch-status` reads only v0.1.2 `etl_batch_commits`. Absence of a row does **not** prove that a pre-v0.1.2 load never committed, since v0.1.1 had no ledger. For legacy data, reconcile manually before replaying; do not blindly generate a new batch ID. A changed API response can change the fingerprint; use stable snapshots for API pipelines.

### Replacement protection example

```yaml
target:
  table: customers_daily
  mode: truncate_insert
  min_replace_ratio: 0.90 # reject input containing fewer than 90% of current rows
  lock_timeout_seconds: 30
policy:
  fail_if_reject_rate_above: 0.05
  allow_empty_replace: false
  allow_partial_replace: false
```

A ratio threshold is optional. Choose a realistic bound based on expected business changes; it does not replace independent row-count checks or source-versus-target reconciliation. To deliberately replace with an empty dataset you must explicitly set `policy.allow_empty_replace: true`, and the direct loader additionally requires `allow_empty_replace=True`.

## Existing SCD2 target migration

**New** SCD2 tables include the active-row generated-column uniqueness guard automatically. **Existing v0.1.1 SCD2 tables are not silently ALTERed**. Regular loads reject them until an operator performs an explicit migration:

1. Back up the existing table and confirm all other writers are stopped (maintenance window).
2. Check that the business key is NOT NULL and no key has more than one `is_current=1` row. If duplicate active rows already exist, correct them before migration.
3. Run `etlguard upgrade-scd2 path/to/scd2-pipeline.yaml --yes` using a database principal with schema-change permissions. The command adds the stored generated marker and a unique index; MySQL DDL commits independently and cannot be rolled back together with an ETL job.
4. Run the disposable MySQL 8 integration tests and perform a sample update, no-change replay and a concurrent-update check in staging.

Do not enable the upgrade command in an automatic scheduler. Its MySQL schema modification can lock a large table. Coordinate the maintenance window, measure lock impact, and take a verified backup first.

## Observability and rollback procedure

- Alert on CLI exit codes `1` (quality/load failure), `2` (invalid configuration), persistent warnings, unknown commit outcomes, or unexpected row-count changes. Persist stdout/stderr, the batch UUID and JSON summary in restricted-access job storage.
- On a failed or interrupted job, run `etlguard batch-status <uuid>` **before** deciding how to retry. If committed, do not replay under a new UUID. If not committed and the input snapshot is identical, retry with the original UUID.
- If a run failed after an audit or database connection error, inspect `etl_run_audit` and `etl_batch_commits`, reconcile target row counts, and escalate any mismatch. A success ledger row means the target commit was acknowledged by InnoDB; the database is the source of truth rather than local report files.
- Transactional failures before commit roll back staged and target DML automatically. A committed replacement or SCD2 change requires an approved restore/compensating migration; it cannot be undone by `ROLLBACK` after commit.
- Keep MySQL binary logs and backups per your RPO/RTO. Restrict database credentials, filesystem reports and rejection files, because they may contain sensitive source values. The library still holds the validated batch in memory and is **not yet a bounded-memory streaming loader** for massive feeds.

## Go/no-go criteria

- [ ] All MySQL 8 integration tests pass in CI and on the actual staging configuration.
- [ ] All business keys are NOT NULL, required indexes exist, and all participating tables use InnoDB.
- [ ] Existing SCD2 targets are migrated after backup and duplicate-current checks.
- [ ] Concurrent SCD2, rollback, duplicate-retry, changed-input, replacement, and connection-failure scenarios are verified in staging.
- [ ] Scheduler persists batch IDs and immutable input snapshots; monitoring alerts and failure recovery procedure are in place.
- [ ] Security and scale limits are reviewed for the real data volumes, report retention and access rights.

**Until all checks are completed, v0.1.2 is a production-hardening *candidate*, not a certified production deployment.**

## Follow-on staging gate

Use the separate [MySQL 8 staging gate and controlled-pilot checklist](mysql8-staging-and-pilot-gate.md) to execute the tests against a disposable MySQL 8 database and collect auditable go/no-go evidence. No production certification is implied by shipping the verification kit.
