# ETL Validation Toolkit v0.1.2: shadow pilot and promotion gate

**Date:** 10 November 2026, results updated 15 November 2026
**Release:** v0.1.2 verification add-on (no runtime version change)
**Status:** Passes the MySQL 8 staging gate on MySQL 8.0 and 8.4. Ready for a supervised pilot; not approved for unattended production scheduling.

## Delivered changes

1. **Enhanced MySQL 8 staging gate** — now refuses MySQL without strict SQL mode, users with grants outside the disposable schema, and pre-existing non-InnoDB tables. Existing requirements for MySQL 8, schema name confirmation and isolated test tables remain.
2. **Shadow pilot planner** (`scripts/prepare_shadow_pilot.py`) — makes a `pilot_` target configuration in an output directory, saves a canonical stable batch UUID, hashes original input/configuration and does not permit overwriting the manifest.
3. **Shadow pilot wrapper** (`scripts/run_shadow_pilot.py`) — local dry validation by default, only permits a shadow database write after a **passing MySQL 8 staging report**, explicit target schema confirmation, restricted grants and a safe preflight. Repeated runs use the same batch UUID.
4. **Human promotion evidence assessor** (`scripts/assess_promotion.py`) — requires passing MySQL8 suite/coverage/Ruff/mypy, actual operational evidence and all three owner approvals. Its strongest decision is **review-ready, not authorized**; it never deploys anything.
5. **Manual GitHub Actions MySQL 8.0 / 8.4 workflow** — provisioned-test integration gate plus isolated shadow first-load / stable retry smoke test.
6. **Offline SQL DDL tests** — compile SCD2 generated active-row uniqueness, upsert business-key uniqueness, and transactional audit/ledger table definitions for the MySQL dialect.
7. **Disposable Docker Compose second schema** — `etlguard_shadow_ci` has dedicated local-only test credentials, separate from the integration test schema `etlguard_ci`.
8. **Evidence template + runbook** — `docs/pilot-evidence-template.json` and `docs/shadow-pilot-and-promotion.md`.

## Results

| Check | Where | Result |
| --- | --- | --- |
| MySQL 8 staging gate: MySQL 8.0 and 8.4 × Python 3.11 and 3.13 | [GitHub Actions](https://github.com/theraghavraman/etl-validation-toolkit/actions/runs/43679925453) | **Passed** in all four jobs |
| MySQL 8 integration tests (upsert, SCD2, concurrency, migration, rollback, audit, retry) | same run | **Passed**, 0 skipped |
| Full test suite with branch coverage (85% floor) | same run | **Passed** |
| Ruff and mypy | same run | **Passed** |
| Disposable shadow first load and idempotent replay | same run | **Passed** |
| Offline staging and pilot safety suite | same run, and locally | **Passed** (27 tests) |
| Synthetic clean CSV shadow validation | local | **Passed**: 3 read, 3 valid, no database writes |

The gate fails if any test is skipped, so a pass means every MySQL test actually ran against a real server.

## Not yet done

These need a real target environment and its owners, so they sit outside what CI can show:

- a backup and restore rehearsal on the target database
- a shadow pilot on real input data, with reconciliation against the source
- written approval from the data, database and operations owners

The promotion assessor below checks that this evidence exists before anyone schedules unattended runs.

## Reproduce the gate locally

```bash
python -m pip install -e '.[dev]'
docker compose -f docker-compose.mysql8-staging.yml up -d
export ETLGUARD_TEST_MYSQL_URL='mysql+pymysql://etlguard_ci:etlguard_ci_local_only@127.0.0.1:3308/etlguard_ci'
python scripts/verify_mysql8_staging.py --confirm-disposable-schema etlguard_ci --execute-tests --report staging-gate-report.json
```

Or run **Actions > MySQL 8 Staging Verification > Run workflow**. Full steps, including the shadow pilot commands, are in the [staging gate and pilot guide](mysql8-staging-and-pilot-gate.md).

## Promotion evidence

Copy `docs/pilot-evidence-template.json` into a protected evidence folder and attach real backup/restore logs, shadow reports, retry and concurrency evidence, monitoring and signed operational approvals. Then run:

```bash
python scripts/assess_promotion.py --staging staging-gate-report.json --evidence pilot-evidence/pilot-evidence.json --report promotion-review.json
```

Even if this returns `review_ready_not_authorized`, a separate signed human change approval is required before a supervised production pilot. **Keep unattended ETL scheduling disabled until this approval exists and the supervised pilot is accepted.**

## Limits

- The v0.1.2 wheel itself is unchanged; this release adds verification scripts, tests, documentation, CI and Compose configuration.
- CI runs against disposable MySQL containers, not a customer or production database.
- The demo credentials are for the local throwaway database only.
- The evidence assessor checks that evidence files exist and records their checksums. It does not authenticate people or approvals, so a person still reviews them.
