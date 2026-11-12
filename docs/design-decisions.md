# Design and ETL operations decisions

## Separation of concerns

Source adapters return `SourceRecord` objects with immutable lineage. The cleaner operates on dictionaries without pandas, HTTP or SQL dependencies. The validator uses Pydantic v2 models. Dataset checks and reject serialization are also database-independent. This allows the same code to run under CPython in the CLI and under Pyodide inside the browser.

## Auditability

Every batch gets a UUID. A record stores source name, logical row number, UTC ingestion timestamp and batch UUID. Before merge, these are persisted in the staging table. The reject CSV/JSON stores the batch, source row number, field, type, message, original value and original source record. The run summary stores rows read/cleaned/valid/rejected/loaded, duplicate counts, error counts by field and type, duration, output file paths, policy failures, commit state, request hash and replay status. Rows rejected counts distinct records; multiple field errors from a record produce multiple reject-file lines.

## Transaction design

MySQL DDL can implicitly commit, so table creation and schema verification are deliberately performed before the load transaction. One SQLAlchemy transaction encloses staging insert, target merge, row-count reconciliation, staging cleanup, durable batch-commit marker and successful audit. MySQL target writers are serialized through a connection-scoped advisory lock, and all participating tables must use InnoDB. `truncate_insert` uses transactional `DELETE` rather than DDL `TRUNCATE`. A reconciliation mismatch throws and rolls back the DML operations; the audit writer uses a separate connection/transaction to record a failed job when possible.

MySQL upsert `rowcount` can be 0, 1 or 2 per row depending on flags and whether values changed, so the loader counts existing business keys *before* the merge instead. `rows_updated` includes matched but unchanged rows, and `rows_unchanged` is separately disclosed for SCD2. Thus `rows_valid = rows_inserted + rows_updated` has one consistent meaning. The loader refuses upsert without a business-key UNIQUE constraint (which is created for new tables).

## SCD Type 2

A new business key creates the first current version. An existing key whose tracked attributes changed causes the previous current row to be closed and a new current version inserted, with a common UTC boundary. Unchanged rows cause no historical insert. `effective_to` is an exclusive boundary and is null for current rows. MySQL writers using ETL Guard's loader are serialized with a bounded advisory lock. A stored computed active marker and UNIQUE constraint provide a database-level single-active-version guarantee; external writers should still observe the same lock protocol for correct ordering. Legacy SCD2 schemas require an explicit migration. For dimensions with enormous history, a set-based merge and appropriately indexed columns are recommended.

## Scale and data safety boundaries

CSV is streamed with Python's strict CSV reader, preserving headers and checking record widths; validation currently materializes validated candidates and cleaned rows in memory to support global uniqueness/null-percentage rules. This is not an out-of-core, distributed ETL engine. Very large feeds need streaming-to-disk or a staging-backed validation design. The REST connector accepts a base GET URL with page, offset or next-link pagination, bounded retries and max-pages safeguards. The browser demo accepts up to 5 MB CSV files and stores validated rows in memory-only SQLite. The frontend never sends the user CSV to a backend, though the Python runtime and Pydantic are downloaded from a public CDN.

## Security hygiene

Never commit API tokens or database passwords. Use `bearer_token_env` and `database_url_env` names in YAML; actual secrets are provided as environment variables. The Docker Compose passwords and loopback port are **local-only demo credentials**, not production defaults. Using a privileged MySQL account or pointing a pipeline at an existing target can be destructive, especially with `truncate_insert`: review configs before running and use least-privilege credentials.

## Explicit non-goals

- Event orchestration, CDC, automatic retries for failed loads, distributed state management.
- Automatic schema migration on ETL load (an operator-invoked guarded SCD2 upgrade is available).
- Cross-database transaction support, unmanaged external SCD2 writers, or deterministic SCD2 effective timestamps supplied by source systems.
- Running SQL writes to MySQL from Pyodide; the browser uses SQLite only.
