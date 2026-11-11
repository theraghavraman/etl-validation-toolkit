# ETL Validation Toolkit v0.1.2 — Shadow Pilot / Promotion Gate Handoff

**Date:** 10 November 2026  
**Release:** v0.1.2 — verification add-on to existing wheel (NO runtime version change)  
**Production decision:** **NO-GO.** No real MySQL 8 instance was available for the integration/pilot tests in this environment.

## Delivered changes

1. **Enhanced MySQL 8 staging gate** — now refuses MySQL without strict SQL mode, users with grants outside the disposable schema, and pre-existing non-InnoDB tables. Existing requirements for MySQL 8, schema name confirmation and isolated test tables remain.
2. **Shadow pilot planner** (`scripts/prepare_shadow_pilot.py`) — makes a `pilot_` target configuration in an output directory, saves a canonical stable batch UUID, hashes original input/configuration and does not permit overwriting the manifest.
3. **Shadow pilot wrapper** (`scripts/run_shadow_pilot.py`) — local dry validation by default, only permits a shadow database write after a **passing MySQL 8 staging report**, explicit target schema confirmation, restricted grants and a safe preflight. Repeated runs use the same batch UUID.
4. **Human promotion evidence assessor** (`scripts/assess_promotion.py`) — requires passing MySQL8 suite/coverage/Ruff/mypy, actual operational evidence and all three owner approvals. Its strongest decision is **review-ready, not authorized**; it never deploys anything.
5. **Manual GitHub Actions MySQL 8.0 / 8.4 workflow** — provisioned-test integration gate plus isolated shadow first-load / stable retry smoke test. The CI configuration was authored, but not executed here.
6. **Offline SQL DDL tests** — compile SCD2 generated active-row uniqueness, upsert business-key uniqueness, and transactional audit/ledger table definitions for the MySQL dialect.
7. **Disposable Docker Compose second schema** — `etlguard_shadow_ci` has dedicated local-only test credentials, separate from the integration test schema `etlguard_ci`.
8. **Evidence template + runbook** — `docs/pilot-evidence-template.json` and `docs/shadow-pilot-and-promotion.md`.

## Local execution evidence

| Check | Result |
| --- | --- |
| Main Python suite | **113 passed, 15 skipped** |
| Branch coverage | **86.43%** (85% configured minimum) |
| Offline staging / pilot safety suite | **27 passed** |
| Python source syntax/bytecode | **Passed** |
| Synthetic clean CSV shadow validation | **Passed** — 3 read / 3 valid, no DB writes |
| Real MySQL 8 staging gate | **BLOCKED** — no server/URL in this environment |
| Promotion evidence assessment | **BLOCKED** — 23 outstanding requirements |
| Live MySQL 8 integration tests | **13 skipped** — no server available |
| Optional API / property-based test modules | **2 skipped** — `respx` / `hypothesis` unavailable |
| Ruff / mypy | **Not executed** — tools unavailable locally; included in CI |
| Shadow DB write / retry | **Not executed** — needs real isolated MySQL 8 |
| Backup/restore, real pilot and manual approvals | **Not executed** — must be handled by owners in their infrastructure |

**Interpretation:** All runnable local checks passed. SQL compilation proves that the expected SQL is generated; it does **not** prove that MySQL accepts it or that it behaves correctly under concurrent requests.

## Execute next, on a disposable MySQL 8 runner

### Using Docker (local-only)

```bash
python -m pip install -e '.[dev]'
docker compose -f docker-compose.mysql8-staging.yml up -d
export ETLGUARD_TEST_MYSQL_URL='mysql+pymysql://etlguard_ci:etlguard_ci_local_only@127.0.0.1:3308/etlguard_ci'
python scripts/verify_mysql8_staging.py --confirm-disposable-schema etlguard_ci --execute-tests --report staging-gate-report.json
```

If and only if **all gates pass**, prepare and validate an immutable synthetic shadow pilot:

```bash
python scripts/prepare_shadow_pilot.py plan --config src/etlguard/examples/pipelines/customers_clean.yaml --output pilot-demo
python scripts/run_shadow_pilot.py --manifest pilot-demo/shadow-manifest.json --validate --report pilot-demo/validation.json
export ETLGUARD_SHADOW_MYSQL_URL='mysql+pymysql://etlguard_shadow:etlguard_shadow_local_only@127.0.0.1:3308/etlguard_shadow_ci'
python scripts/run_shadow_pilot.py --manifest pilot-demo/shadow-manifest.json --execute --confirm-shadow-schema etlguard_shadow_ci --staging-report staging-gate-report.json --report pilot-demo/first-run.json
```

Repeat the last command with the same manifest for an idempotent replay. Check that `replayed` is true, counts are unchanged, audit and commit ledger match, and no other tables were changed. **Never do this with production credentials.**

Alternatively, in an access-controlled repository, manually trigger `.github/workflows/mysql8-staging-gate.yml`. Its four matrix jobs run both MySQL 8.0 and 8.4 on Python 3.11 and 3.13, plus the disposable shadow smoke; archive the resulting CI artifacts. No CI execution occurred in this session.

## Promotion evidence

Copy `docs/pilot-evidence-template.json` into a protected evidence folder and attach real backup/restore logs, shadow reports, retry and concurrency evidence, monitoring and signed operational approvals. Then run:

```bash
python scripts/assess_promotion.py --staging staging-gate-report.json --evidence pilot-evidence/pilot-evidence.json --report promotion-review.json
```

Even if this returns `review_ready_not_authorized`, a separate signed human change approval is required before a supervised production pilot. **Keep unattended ETL scheduling disabled until this approval exists and the supervised pilot is accepted.**

## Important limits

- The original v0.1.2 installable wheel remains unchanged; source ZIP updates only add verification scripts, tests, documentation, CI and Compose configuration.
- No connection to a real MySQL 8 server, production database, external CI service or data customer environment was made.
- No production release, schema migration, deployment, backup/restore or rollback action was performed.
- Local demonstration credentials are for the disposable loopback-only database and must never be reused in a shared environment.
- The evidence assessor does not authenticate people, file provenance, or approvals. It only checks presence and records checksums; human review is required.
