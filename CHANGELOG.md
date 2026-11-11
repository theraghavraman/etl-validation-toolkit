# Changelog

## 0.1.2 — production hardening candidate (2026-11-10)

- Durable `etl_batch_commits` ledger and atomic target+success-audit commit using InnoDB transactions.
- Stable UUID retries via CLI `--batch-id`; input/configuration fingerprints prevent changed batches reusing an ID.
- Recovered outcomes after lost commit acknowledgements when the durable ledger confirms success.
- `batch-status` for operator commit status and failed-audit context.
- Serialized per-target MySQL writers via bounded `GET_LOCK`/`RELEASE_LOCK`; database-enforced single-active-version SCD2 uniqueness with explicit migration command for older tables.
- NOT NULL business-key enforcement and InnoDB storage-engine preflight for transactional safety.
- Fail-closed empty replacement for low-level loaders; optional `min_replace_ratio` against existing row counts.
- Partial source-read counts retained; incomplete feeds never load. Report write failures after commit are warnings.
- Rejected CSV formula escaping (JSON remains lossless), and expanded SQLite/MySQL 8 regression suite.
- Real MySQL 8 testing remains a promotion gate and is NOT certified by local tests.


All notable changes to this repository are documented here.

## [0.1.1] - 2026-11-10

### Fixed

- Count every invalid source record, even when many have identical cleaned values.
- Block unsafe empty and partially rejected `truncate_insert` batches by default; overrides require explicit policy opt-in.
- Validate pipeline names before using them as report file basenames.
- Refuse MySQL upserts when competing UNIQUE indexes could update the wrong business record.
- Honor rename_map when snake_case_columns is disabled.
- Add dedicated regression tests and update the packaged version.

## [0.1.0] - 2026-11-10

### Added

- Pluggable CSV, Excel and paginated REST API readers with lineage metadata.
- Configurable deterministic cleaning and typed Pydantic v2 row/dataset checks.
- Rich CLI with dry-run, rejects, JSON summaries and configurable failure policy.
- MySQL 8 staging-to-target append, transactional replacement, upsert and SCD2 modes.
- Count reconciliation, durable ETL run audit and reusable Python API.
- Pyodide + SQLite browser demo powered by the same Python validation engine.
- Unit, property, mocked API, CLI and MySQL service integration tests.

Historical v0.1.0 and v0.1.1 artifacts are preserved separately. Promote or tag v0.1.2 only after the MySQL 8 integration suite and production-hardening acceptance checklist pass.

### v0.1.2 — Staging and controlled-pilot verification add-on (source ZIP only)

- Added a non-overwriting shadow pilot plan and immutable file/config checksums with stable UUID.
- Added a fail-closed shadow-only runner: dry validation by default; explicitly confirmed
  disposable MySQL 8 shadow schema, scoped grants, strict SQL mode, no untracked tables.
- Added evidence-based promotion review which **never** authorizes production.
- Expanded MySQL8 staging preflight to check strict SQL mode, schema-only privileges and
  existing table engine; added MySQL DDL compilation and offline safety tests.
- Docker Compose config now provisions a second synthetic shadow-only test schema.
- This add-on does **not** change the v0.1.2 installable Python wheel/runtime.

### v0.1.2 — Phase 3 independent shadow reconciliation (source ZIP only)

- Added read-only source-vs-target field-level reconciliation on isolated MySQL 8
  shadow tables, along with audit/ledger/staging consistency checks.
- Added physical target SHA-256 row fingerprints across stable-ID retries.
- Updated disposable MySQL CI smoke to enforce checks before and after retry.
- Added SQLite fault-injection tests for values, audits, ledger, staging rows,
  SCD2 unexpected history, source checksum changes, and retry invariants.
- The v0.1.2 runtime/wheel is unchanged. Real MySQL integration, backups,
  production pilot and human sign-off remain outstanding.
