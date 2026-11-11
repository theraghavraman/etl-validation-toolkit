# ETL Validation Toolkit v0.1.2 — Phase 2: Shadow Pilot and Promotion Review

**Status: NOT production-certified.** These tools are for controlled verification. No production database writes are performed automatically. The original `etlguard` Python runtime and wheel stay at v0.1.2.

## Prerequisites

- Python 3.11+ and `python -m pip install -e '.[dev]'` from the extracted source.
- A passing MySQL 8 staging gate from `scripts/verify_mysql8_staging.py` on a disposable MySQL 8 instance. The gate now also requires **strict SQL mode**, **schema-scoped grants** and **all existing test tables InnoDB**.
- An immutable local CSV/Excel snapshot. API feeds must be captured to a controlled snapshot first.
- Operator-approved source schema, row counts, target scope and changes. No live/customer data is needed to run the synthetic example below.

## Step 1 — Start a disposable MySQL 8 and run the automated staging gate

```bash
python -m pip install -e '.[dev]'
docker compose -f docker-compose.mysql8-staging.yml up -d
export ETLGUARD_TEST_MYSQL_URL='mysql+pymysql://etlguard_ci:etlguard_ci_local_only@127.0.0.1:3308/etlguard_ci'
python scripts/verify_mysql8_staging.py --confirm-disposable-schema etlguard_ci --execute-tests --report staging-gate-report.json
```

On Windows PowerShell use `$env:ETLGUARD_TEST_MYSQL_URL = 'mysql+pymysql://etlguard_ci:etlguard_ci_local_only@127.0.0.1:3308/etlguard_ci'`. The Docker Compose setup is disposable, bound to loopback, and now provisions a **second shadow-only schema** `etlguard_shadow_ci` with user `etlguard_shadow`. Passwords in these examples are intentionally public local-test credentials and must **never** be used for production or in a shared network.

The staging script exits `0` only when all integration, full pytest, Ruff and mypy checks pass with **zero skipped tests**. Record its JSON report and logs. **Do not proceed to a database write when this gate fails.**

**Without local Docker:** Upload this source to an access-controlled GitHub repository, open **Actions → MySQL 8 Staging Verification → Run workflow**. The included manually dispatched workflow runs MySQL 8.0 and 8.4 with Python 3.11/3.13. After passing the staging gate it runs a disposable shadow smoke test that exercises the first load and the same-UUID retry; it exports `shadow-smoke-report.json` alongside the gate logs. The workflow does not connect to a production database. No claim of a CI pass is made until it actually executes and publishes its logs. The separate public-demo Pages deployment is disabled by default unless repository variable `ENABLE_PUBLIC_DEMO_PAGES` is explicitly set to `true`.

## Step 2 — Prepare a pinned synthetic shadow pilot (NO database writes)

```bash
python scripts/prepare_shadow_pilot.py plan \
  --config src/etlguard/examples/pipelines/customers_clean.yaml \
  --output pilot-demo
python scripts/prepare_shadow_pilot.py verify --manifest pilot-demo/shadow-manifest.json
python scripts/run_shadow_pilot.py --manifest pilot-demo/shadow-manifest.json --validate \
  --report pilot-demo/shadow-validation.json
```

This creates `shadow-pipeline.yaml` and `shadow-manifest.json` containing a **single persistent batch UUID**, SHA-256 hashes of the original YAML, original CSV/Excel and generated shadow YAML, and a `pilot_` prefixed shadow target. The generated YAML is constrained to `ETLGUARD_SHADOW_MYSQL_URL` and disables permissive empty/partial replacement options. Do not overwrite, regenerate or edit these artifacts on retries. For another input, prepare a **new pilot output directory**, with a new UUID. The planner refuses API feeds, zero-byte files, unsafe target identifiers, and `truncate_insert` unless explicitly authorized for an isolated shadow table.

The `--validate` operation reads the snapshot and writes local validation reports **without contacting any database**. Investigate any rejection or source problem before doing Step 3.

## Step 3 — Perform an approved, isolated shadow-only write

First verify the MySQL 8 staging gate in Step 1 passed. Use a fresh local shadow schema; the included disposable Compose example initializes it automatically. In a staging environment that uses its own server, expose only the **separately provisioned disposable shadow schema** through a localhost SSH tunnel; give the dedicated shadow identity privileges *only* on that schema.

```bash
export ETLGUARD_SHADOW_MYSQL_URL='mysql+pymysql://etlguard_shadow:etlguard_shadow_local_only@127.0.0.1:3308/etlguard_shadow_ci'
python scripts/run_shadow_pilot.py --manifest pilot-demo/shadow-manifest.json \
  --execute --confirm-shadow-schema etlguard_shadow_ci \
  --staging-report staging-gate-report.json \
  --report pilot-demo/shadow-first-run.json
```

PowerShell:

```powershell
$env:ETLGUARD_SHADOW_MYSQL_URL = 'mysql+pymysql://etlguard_shadow:etlguard_shadow_local_only@127.0.0.1:3308/etlguard_shadow_ci'
python scripts/run_shadow_pilot.py --manifest pilot-demo/shadow-manifest.json --execute --confirm-shadow-schema etlguard_shadow_ci --staging-report staging-gate-report.json --report pilot-demo/shadow-first-run.json
```

`run_shadow_pilot.py` requires a **real passing MySQL 8 staging report**, verifies the manifest, runs validation first, then refuses root/admin credentials, remote database endpoints, non-disposable schema names, out-of-schema grants, non-InnoDB tables, a pre-existing untracked target, unrelated rows in the commit ledger, leftover staging rows and missing success audits. It accepts only genuine MySQL 8 with strict SQL mode. The generated shadow target is isolated from the original configured table. It **does not itself certify that the server is production-independent** — the operator must independently confirm that this is a disposable staging instance.

Do not bypass these safety checks by directly invoking `etlguard run` on a production connection. After a successful shadow run, repeat the **same command with exactly the same manifest**. The second result must have `"replayed": true` and must not change target rows. A changed CSV/Excel or YAML checksum fails verification before a database write. Preserve both run reports and `etlguard batch-status <batch_uuid> --database-url-env ETLGUARD_SHADOW_MYSQL_URL` output as evidence. This does **not** replace a separate simulated lost-commit test, SCD2 concurrency test or verified backup/restore rehearsal.

### Phase 3 — Independently verify persisted rows and unchanged retry fingerprint

After the initial `--execute`, run the new **read-only** verifier, then repeat
the same batch ID and run it again. Both reports must pass and have identical
`target_physical_sha256` values. The checker detects same-count row corruption
that a simple `rows_loaded` check would miss. See
`docs/phase-3-shadow-reconciliation.md` for the exact commands.

## Step 4 — Assemble evidence; assess readiness for human approval

Copy `docs/pilot-evidence-template.json` to an evidence folder as `pilot-evidence.json`. Attach the actual logs, reconciliations, backup-restore and incident-recovery evidence and signed owner approvals under that **same directory**. After independently verifying each artifact, set the relevant `verified` field to `true` and set `evidence_file` to the file name. Do not use placeholder documents to mark a check complete.

```bash
python scripts/assess_promotion.py \
  --staging staging-gate-report.json \
  --evidence ./pilot-evidence/pilot-evidence.json \
  --report promotion-review.json
```

The assessor requires all four automated checks to pass with zero skips, real MySQL 8 preflight details, a staging report no older than 30 days, required operational evidence and **data-owner, database-owner and operations-owner approval artifacts**. It records SHA-256 digests of available evidence files. The possible decisions are:

- `blocked`: required evidence/test results are missing, failed, stale or skipped. Exit code 2.
- `review_ready_not_authorized`: supplied evidence is complete enough to **send to human change-control reviewers**, but no production authorization or database write is issued. Exit code 0.

**Crucial limitation:** the tool cannot authenticate signatures, independently verify the truth of uploaded screenshots/reports, prove backup recovery or grant permission to operate production. People with operational responsibility must review these artifacts independently and grant explicit change authorization.

## Step 5 — Supervised production pilot only after human sign-off

A separate authorized deployment team must confirm the actual production schema, table, write mode, source snapshot, schedule, tested backup/PITR restore, performance/lock budget, monitoring and rollback owner. Start with a single supervised job, record before-and-after counts and ledger state and leave the cron/scheduler disabled until owners approve. A pilot that fails must be investigated; never substitute a new UUID to retry an uncertain commit. For `truncate_insert`, never use a partial data sample against a live target.

## Known boundaries and recovery

- This kit **cannot run a real MySQL 8 service in the current artifact-building environment**. An external MySQL 8 staging instance is needed for the 13 database integration tests and the shadow load.
- Local automated checks do not test infrastructure-specific timeouts, replication lag, cloud privileges, backup/PITR or actual workload performance.
- A successfully committed transaction cannot be undone with a later `ROLLBACK`. Use approved compensating actions or independently rehearsed restore/PITR.
- Source and config SHA-256 checks detect changes between checks; protect shared source files against concurrent edits while the load is running.
- This kit deliberately never issues a production-ready or GO decision automatically.
