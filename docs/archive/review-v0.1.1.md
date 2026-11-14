# ETL Validation Toolkit — Code Review, Test Results & Patch Handover

**Review date:** 10 November 2026  
**Original artifacts:** `etl-validation-toolkit-v0.1.0.zip`, `etl_validation_toolkit-0.1.0-py3-none-any.whl`  
**Reviewed outputs:** `etl-validation-toolkit-v0.1.1-reviewed.zip`, `etl_validation_toolkit-0.1.1-py3-none-any.whl`  
**Release classification:** Reviewed local candidate; **not certified production-ready**.

## Executive summary

The project is a well-structured Python ETL data-quality framework with CSV, Excel and REST API sources; cleaning; Pydantic row validation; dataset checks; rejected-row CSV/JSON; a CLI; MySQL staging/upsert/transactional replacement; and SCD Type 2 history. It also contains a static browser demo, test suite, example datasets, configuration reference and CI workflows.

The uploaded original wheel's 33 package and resource files were byte-for-byte identical to the corresponding original source files. The original wheel installed locally without fetching dependencies; its CLI launched and `validate .../customers_clean.yaml` passed.

An important false-pass condition was reproduced in the original: 100 identical invalid records gave `rows_rejected=1`, `duplicates_dropped=99` and allowed a 5%-threshold quality gate to pass. A destructive replacement operation was also demonstrated in SQLite: loading zero validated records into `truncate_insert` deleted an existing target row. The reviewed 0.1.1 patch addresses both risks and three additional safety/correctness issues.

## Patches included in v0.1.1

| ID | Severity | Finding | Reviewed fix and regression |
| --- | --- | --- | --- |
| F-01 | High | Identical invalid rows were deduplicated before validation, understating rejection rates. | Deduplication is now limited to accepted rows after Pydantic and dataset-level checks. Tests cover invalid model rows, invalid foreign keys and repeated unique-key violations. |
| F-02 | High | `truncate_insert` could replace the entire target with empty or partially rejected data. | Replacement now fails on an empty input or any rejects unless the operator explicitly enables `policy.allow_empty_replace` / `policy.allow_partial_replace`. |
| F-03 | High | MySQL `ON DUPLICATE KEY UPDATE` can update the wrong business record if the target has multiple competing unique indexes. | Preflight rejects competing uniqueness constraints beyond the declared business key and ETL primary key. |
| F-04 | Medium | Pipeline identifiers could contain directory separators and affect generated report paths. | Added filename-safe pipeline identifier validation. |
| F-05 | Medium | `rename_map` was silently ignored when `snake_case_columns: false` used non-normalized source headers. | The rename map now respects column-normalization mode. |

**Replacement policy compatibility note:** Existing pipelines using `truncate_insert` on empty or partially rejected feeds now fail closed by default. Opt-ins are available, but should be enabled only after checking the intended data-loss semantics. Other loading modes retain their original rejection-policy behavior.

## Testing performed

| Check | Original v0.1.0 | Reviewed v0.1.1 |
| --- | --- | --- |
| Wheel vs matching source contents | 33 of 33 match | Verified after build |
| Automated tests run | 81 passed / 10 skipped | **91 passed / 10 skipped** |
| Statement/branch coverage (`pytest-cov`, aggregate) | 89% | **90%** |
| CLI startup / `--help` | Passed | Passed |
| Example clean CSV validation | Passed | Passed |
| Reproduce repeated-invalid false-pass | Reproduced | Fixed; regression passes |
| SQLite-backed loader smoke tests | Passed | Passed |
| Local wheel build | Original wheel provided | Built successfully offline |
| Python compile check | Not required | Passed |
| Actual MySQL 8 end-to-end integration | Not run | **Not run** |

Ten skipped test cases consist of eight MySQL integration tests (no MySQL test instance), plus two tests/modules requiring optional `respx` and `hypothesis` packages. The environment could not download additional packages, so these checks are **not** claimed as completed. The project includes workflows to run them in a properly provisioned CI environment.

## Remaining production-readiness concerns

1. **Audit failure after committed data load (high):** MySQL target DML commits in `MysqlLoader.load()` before `AuditWriter.write()` executes. If auditing fails, `Pipeline.run()` marks the job failed even though data may already be committed. **Do not automatically retry a failed append load without confirming the batch's commit state**. Design an idempotent load/audit state machine or durable commit marker before unattended production retries.
2. **MySQL integration and schema safety (high):** Full tests for transaction behavior, MySQL-specific upsert, index inspection and SCD2 still need a real MySQL 8 server. Avoid using a privileged DB account. Validate schemas and indexes on representative existing tables.
3. **Concurrent SCD2 writers (high):** This implementation deliberately assumes single-writer execution per target; coordinate jobs through a scheduler/lock to prevent competing current versions.
4. **Data scale (medium):** CSV reading is chunked, but validation stores candidate and clean rows in memory. Large datasets need bounded-memory/spill-to-disk processing.
5. **Reject-file privacy (medium):** Rejected-row reports include raw values and entire source rows, potentially including personal data. Configure restricted report directories, retention limits, encryption at rest and appropriate access controls. Treat reject CSV cells as untrusted when opening them in spreadsheet software.
6. **Report accuracy after mid-stream source failure (medium):** A source failure during iteration can prevent partial row statistics being copied into the run summary. Add a partial-batch accounting test and recovery pathway before relying on audit counts for failed streaming jobs.
7. **Release housekeeping (low):** Repository and demo URLs are placeholders (`YOUR_USERNAME`). Configure actual links, run the full CI workflow (Ruff, mypy, Hypothesis, respx, MySQL), and publish through an owned repository only after approval.

## Install and run

```bash
# Requires Python 3.11+ and network/package access for dependencies
python -m pip install ./etl_validation_toolkit-0.1.1-py3-none-any.whl
etlguard --help

# In the extracted reviewed source folder:
etlguard validate src/etlguard/examples/pipelines/customers_clean.yaml
etlguard validate src/etlguard/examples/pipelines/customers.yaml

# Full verification once dependencies and a MySQL 8 test instance are available:
python -m pip install -e '.[dev]'
ruff check .
mypy src/etlguard
pytest -q --cov=etlguard --cov-branch --cov-report=term-missing --cov-fail-under=85
```

For MySQL integration tests, supply `ETLGUARD_TEST_MYSQL_URL` as documented in `docs/deployment.md`. Keep test credentials isolated and never run destructive replacement tests against a live customer database.

## Conclusion

**Use v0.1.1 in development/staging for further evaluation.** The original v0.1.0 should not be used with untrusted/duplicate data quality gates or destructive `truncate_insert` loads. Promote the reviewed candidate only after the real MySQL, missing optional test, lint/type and operational audit/idempotency checks are addressed.
