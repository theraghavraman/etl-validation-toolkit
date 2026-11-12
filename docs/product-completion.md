# ETLGuard v0.1.4 product scope and acceptance

ETLGuard is delivered as a Python package, source download and live browser application. This document defines the completed product scope. The browser is a usable CSV data-quality lab; production database ingestion runs through the CLI or Python API.

| Capability | Delivered behavior | Verification |
|---|---|---|
| CSV ingestion | Streaming UTF-8/configured-encoding reader, quoted cells, multiline records, custom delimiters, header offsets and source lineage | Source tests, malformed-feed regression |
| Excel ingestion | Worksheet name/index and row offsets, with duplicate/empty headers rejected before pandas can rename them | Excel fixture and header tests |
| REST ingestion | JSON records paths, pagination, retry/backoff and environment-based bearer authentication | Mocked HTTP/pagination/security tests |
| Cleaning | Trim, null tokens, column normalization/renaming, dates, exact decimal conversion, case and accepted-row deduplication | Unit and property tests |
| Validation | Pydantic contracts, cross-field rules, unique business keys, reference sets, row counts and null limits | Engine and schema tests |
| Failed batch handling | Explain row errors; refuse malformed partial feeds and failed dataset/quality gates before loading | Pipeline and CLI tests |
| Reports | CSV/JSON rejects with original row, field and reason; batch summary and reconciled counts | Reports, formula-escaping and count-invariant tests |
| MySQL loading | Append, transactional replacement, upsert and SCD2 through staging tables | Real MySQL integration tests for all four modes |
| Precision | Pydantic Decimal precision/scale in DDL; refuse rounding/overflow in new and existing tables | Exact four-decimal MySQL round trip and narrow-schema refusal |
| Transactions | InnoDB target/staging changes, success audit and durable ledger commit atomically; count mismatch rolls back | Rollback, audit and ledger integration tests |
| Retry and concurrency | Stable UUID, identical replay without new writes, changed-request refusal, commit recovery and target locks | Retry, uncertain-commit and concurrent-writer tests |
| Browser application | Customer/Transaction samples, local CSV upload, schema templates, accepted/rejected views, summary, SQLite count and downloadable reports | Real Chromium desktop/mobile acceptance |
| Browser resilience | Invalid uploads clear stale results; strict UTF-8 and row widths; failed runtime startup can be retried | Input-error and blocked-CDN recovery checks |
| Package usability | Version/help, non-overwriting starter pipeline, Python module entry point and validation without MySQL | Fresh installed-wheel checks outside the checkout |
| Distribution | Wheel, tar.gz, portable source ZIP, platform launchers, user guide and SHA-256 checksums | Package content/source parity checks and clean installation |
| Continuous verification | Lint/types, coverage threshold, browser checks and real MySQL on CI; release waits for all gates | GitHub Actions for the exact release commit |

## Fixes completed during the final audit

- Replaced permissive CSV parsing that could silently discard the first cell of an over-wide row.
- Refused duplicate/empty headers and misspelled dataset-check columns.
- Reserved internal database table names, including the commit ledger.
- Preserved Decimal precision rather than silently rounding values to two places.
- Added browser accepted-row/summary exports, schema templates, UTF-8 checks and runtime recovery.
- Made a wheel installation immediately usable through `etlguard init`, `--version` and `python -m etlguard`.
- Made release publication depend on quality, browser, packaging and the complete MySQL/Python compatibility matrix.

## Supported boundary

Python 3.11 and 3.13 are exercised against MySQL 8.0 and 8.4. All supplied data is synthetic. The browser supports UTF-8 CSV up to 5 MB and the two included schemas; Excel, REST, custom models and MySQL use Python. Browser Python needs CDN access at startup, and its temporary SQLite contents are not persistent.

CSV input is streamed, while accepted records and global checks remain in memory. This is a batch toolkit, without a distributed scheduler or hosted database service. A business deployment needs target-specific volume, performance and recovery validation. PyPI publication is not configured; install the verified release wheel or source.

See the [user guide](USER_GUIDE.md), [release verification report](verification-v0.1.4.md) and [change log](../CHANGELOG.md).
