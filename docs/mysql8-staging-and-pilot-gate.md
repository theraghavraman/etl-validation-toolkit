# ETL Validation Toolkit v0.1.2: MySQL 8 Staging Gate and Controlled Pilot

## Release decision

**NO-GO for unattended production** until real MySQL 8 tests, operational recovery rehearsal, controlled pilot, and approval are complete. This kit does not make production changes and does not turn a local unit-test pass into production certification.

## What this staging gate verifies

- Connects to an expressly confirmed, disposable `etlguard_ci`, `etlguard_test`, or `etlguard_staging_ci` schema. It will not run integration tests on a schema with unrelated tables.
- Refuses MySQL root/admin account names, MySQL 5.7/MariaDB servers, non-InnoDB default engine, read-only servers, wrong schema, and remote hosts unless `--allow-remote` is explicitly provided.
- Runs the repository's 13 MySQL 8 integration tests, complete pytest suite with at least 85% branch coverage, Ruff, and mypy.
- Records command exits and skipped tests in `staging-gate-report.json` and detailed logs. A nonzero status or ANY skipped test prevents a pass.
- MySQL integration tests exercise upsert, SCD Type 2, concurrency, migration, uniqueness, rollback, audit and retry logic.

**Important:** These integration tests CREATE and DROP tables; never point `ETLGUARD_TEST_MYSQL_URL` at customer/staging application schemas or any production schema. This is not a security assessment, performance certification, or proof of real-world RPO/RTO.

## Option A: Local disposable MySQL 8.4 with Docker

From the extracted source directory:

```bash
python -m venv .venv
# On Linux/macOS: source .venv/bin/activate
# On Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'

docker compose -f docker-compose.mysql8-staging.yml up -d
# The example is ONLY available on localhost port 3308 and uses ephemeral tmpfs storage.
export ETLGUARD_TEST_MYSQL_URL='mysql+pymysql://etlguard_ci:etlguard_ci_local_only@127.0.0.1:3308/etlguard_ci'
python scripts/verify_mysql8_staging.py --confirm-disposable-schema etlguard_ci --execute-tests --report staging-gate-report.json
```

For **Windows PowerShell**, set the test URL with:

```powershell
$env:ETLGUARD_TEST_MYSQL_URL = 'mysql+pymysql://etlguard_ci:etlguard_ci_local_only@127.0.0.1:3308/etlguard_ci'
python scripts/verify_mysql8_staging.py --confirm-disposable-schema etlguard_ci --execute-tests --report staging-gate-report.json
```

Exit `0` plus `"status": "passed"` is a staging test pass, not a production deployment approval. Exit `1` means at least one test/quality gate failed; exit `2` means preflight was blocked. For local cleanup, `docker compose -f docker-compose.mysql8-staging.yml down`; the database uses a tmpfs and is never to contain real data. **Do not reuse the example passwords anywhere beyond this local test environment.**

## Option B: Your own disposable MySQL 8 server

1. Create a **new, empty** schema named `etlguard_staging_ci` and a dedicated NON-ROOT account that can create/drop tables only inside that schema.
2. Set `ETLGUARD_TEST_MYSQL_URL` using environment/secret management; do not commit credentials to a repository or send them by email/chat.
3. Run `python scripts/verify_mysql8_staging.py --confirm-disposable-schema etlguard_staging_ci --execute-tests --allow-remote` on the trusted test runner. Omit `--allow-remote` when connecting through a localhost SSH tunnel.
4. Save the JSON report and logs from `mysql8-gate-logs/` in restricted CI artifacts.
5. Run the same suite on the precise MySQL major/minor version, SQL mode, charset, and resource limits used by the intended staging environment. Docker default settings are not necessarily representative.

## Option C: GitHub Actions, with no external database credentials

Copy this project into your own private GitHub repository. Run the included `.github/workflows/mysql8-staging-gate.yml` via **Actions > MySQL 8 Staging Verification > Run workflow**. It provisions disposable MySQL 8.0 and MySQL 8.4 service containers, runs Python 3.11 and 3.13 jobs, and preserves test reports as artifacts. This is a CI compatibility gate only; it is not your application staging database.

## Controlled pilot protocol (manual approval required)

### Phase 0: Preconditions / written sign-off

- [ ] MySQL 8 staging gates GREEN (zero skipped checks); staging schema, charset, SQL mode, and data volumes are representative.
- [ ] Input source is immutable or snapshotted; scheduler stores the UUID *before* the first attempt.
- [ ] Data owner and DB owner have approved the exact pipeline YAML, target schema, writes, and observation window.
- [ ] Verified backup and a timed restore exercise have passed; RPO/RTO are documented.
- [ ] SCD2 older tables are explicitly upgraded during maintenance; no duplicate active rows; all participating tables use InnoDB.
- [ ] Privileged DDL is disabled for regular runtime identities; reject reports and logs have retention/PII access controls.
- [ ] Running jobs and external writers are paused or coordinated, especially when testing SCD2.

### Phase 1: Shadow pilot, isolated target

1. Clone the target schema to a dedicated staging/shadow table; never run `truncate_insert` against the real production table using a sample subset. It would replace the entire target.
2. Run `etlguard validate <pipeline.yaml>` first; inspect row counts, reject reasons, source hash and dataset-level checks.
3. Generate ONE UUID, persist it with the immutable source, and execute `etlguard run <pilot-pipeline.yaml> --batch-id <uuid>` only against the shadow target.
4. Run `etlguard batch-status <uuid>`, compare target counts and business keys, and inspect the `etl_batch_commits` and `etl_run_audit` rows.
5. Retry the SAME input and UUID; verify the committed target does not change. Retry with changed input and SAME UUID; verify it is rejected.
6. Rehearse connection loss before and around COMMIT in staging; verify unknown outcomes are resolved using batch-status, not a newly generated UUID.
7. Re-run concurrent SCD2 updates and verify exactly one active row for every business key, including alongside any non-ETL Guard writers.

### Phase 2: Approved, limited real-target pilot

1. Obtain written change authorization specifying environment, pipeline, exact target, permitted load mode, batch/time limits and a rollback owner.
2. Start with one supervised run during a designated observation window, not unattended cron. For replacement jobs, execute only the approved complete snapshot with independent pre-run row-count comparison.
3. Monitor process exit code, MySQL sessions/locks, validation failures, row deltas, rejection percentage, audit/commit ledger consistency and query performance.
4. Compare downstream business totals to baseline; keep a snapshot of pre-pilot and post-pilot metrics. Hold the scheduler disabled until sign-off.
5. If the result is ambiguous, do not create a fresh batch UUID. Query `batch-status` after DB recovery; reconcile target state. A committed load cannot be undone by simply issuing ROLLBACK.
6. If a committed change must be reverted, use the tested backup/PITR or explicitly approved compensating SQL; have the DB owner lead restoration.

### Phase 3: Go / no-go

**GO** only after all test gates pass, audit/ledger and target reconcile, backup/restore is proved, repeat/recovery is safe, performance is acceptable, and the data owner, DB owner, and operations owner sign off. Otherwise **NO-GO**; keep unattended jobs disabled.

## Gate results template

| Gate | Evidence | Status |
| --- | --- | --- |
| MySQL 8.0 integration | `staging-gate-report.json` + logs | NOT RUN |
| MySQL 8.4 integration | `staging-gate-report.json` + logs | NOT RUN |
| Ruff / mypy | CI logs | NOT RUN |
| Complete pytest suite / coverage | CI logs | NOT RUN |
| SCD2 active-row uniqueness and concurrency | MySQL test logs + verification SQL | NOT RUN |
| Immutable input and durable batch ID | Scheduler configuration evidence | NOT RUN |
| Backup restore rehearsal | Restore log and elapsed time | NOT RUN |
| Shadow pilot and reconciliation | Signed pilot evidence | NOT RUN |
| Live supervised pilot | Change record / monitoring | NOT RUN |
| Production approval | Data owner + DB owner + Operations | NOT APPROVED |

**Never set a gate to PASS from documentation alone.**
