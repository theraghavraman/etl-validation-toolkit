# Phase 3: Read-only shadow-table reconciliation and retry integrity

**Release:** ETL Validation Toolkit v0.1.2, source-only verification extension.  
**Status:** MySQL 8.0/8.4 staging and independent shadow reconciliation passed on Python 3.11/3.13. See [completion evidence](completion-report.md).  
**Safety:** This workflow is ONLY for an approved, disposable MySQL 8 shadow schema.

## Purpose and evidence

A successful ETL exit status or matched row count cannot prove that the target
contains the correct values. Phase 3 adds `scripts/verify_shadow_reconciliation.py`,
which independently re-reads the immutable CSV/Excel snapshot, repeats validation,
and compares **all modeled values of every record** with the persisted target.

The verifier checks:

- The source/config/checksum manifest still matches the originally prepared pilot.
- A real passing MySQL 8 staging report is available and current.
- Only a localhost/SSH-tunneled, dedicated non-root, disposable shadow schema is used.
- A durable committed batch, corresponding success audit, and zero uncommitted
  staging rows exist.
- Source record counts, valid/rejected counts, source request hash, batch ledger
  counts and audit totals agree.
- No missing, extra, modified or schema-incompatible model rows exist in target.
- For a first-load SCD2 table, exactly one active record per key and no unexpected
  historical versions exist.
- A stable SHA-256 fingerprint of the *physical target rows*, including ETL IDs,
  remains unchanged across an identical retry.

The JSON report contains **counts, mismatch categories, identifiers and hashes**,
not raw customer rows, email addresses or database passwords. Protect the report
as operational metadata; hashes do not make the report a digital signature.

## Run on a separate, disposable MySQL 8 staging instance

1. Use the earlier `docs/shadow-pilot-and-promotion.md` to run the staging gate
   successfully and prepare a `pilot-demo/shadow-manifest.json` pinned to an
   immutable source snapshot.
2. Set `ETLGUARD_SHADOW_MYSQL_URL` to a dedicated `etlguard_shadow_ci` user/schema
   on localhost (or a localhost SSH tunnel); never set production credentials.
3. Run one explicitly authorized shadow load:

```bash
python scripts/run_shadow_pilot.py --manifest pilot-demo/shadow-manifest.json \
  --execute --confirm-shadow-schema etlguard_shadow_ci \
  --staging-report staging-gate-report.json \
  --report pilot-demo/first-run.json
```

4. Verify the **actual persisted rows** with this new read-only command:

```bash
python scripts/verify_shadow_reconciliation.py \
  --manifest pilot-demo/shadow-manifest.json \
  --staging-report staging-gate-report.json \
  --confirm-shadow-schema etlguard_shadow_ci \
  --report pilot-demo/reconciliation-before-retry.json
```

5. Repeat the load with the same `--manifest` and save to
   `pilot-demo/identical-retry.json`. The load result must say `"replayed": true`.
   Run `verify_shadow_reconciliation.py` again with output
   `pilot-demo/reconciliation-after-retry.json`.
6. Compare the two reports: both must have `"status": "passed"`, zero mismatches
   and **exactly the same `target_physical_sha256`**. Retain the source manifest,
   staging gate, first/retry load reports, and both reconciliation reports in
   access-controlled deployment evidence. These reports can support the
   `shadow_row_reconciliation` and `same_batch_retry` operational checks, only
   after an independent operator verifies they are genuine.

### PowerShell example

```powershell
$env:ETLGUARD_SHADOW_MYSQL_URL = 'mysql+pymysql://etlguard_shadow:etlguard_shadow_local_only@127.0.0.1:3308/etlguard_shadow_ci'
python scripts/verify_shadow_reconciliation.py --manifest pilot-demo/shadow-manifest.json --staging-report staging-gate-report.json --confirm-shadow-schema etlguard_shadow_ci --report pilot-demo/reconciliation-before-retry.json
```

The password in this example is an intentional public fixture valid only for the
local disposable Compose database. **Never reuse it on shared or production hosts.**

## Automated GitHub Actions shadow smoke

The existing `.github/workflows/mysql8-staging-gate.yml` now also runs independent
reconciliation **before and after the same-UUID retry**, via
`scripts/ci_shadow_smoke.py`. The CI job fails if rows mismatch or the physical
fingerprint changes; its report includes summarized reconciliation evidence.
The workflow must be **manually triggered**; merely having the YAML file does
not mean the MySQL tests have been executed.

## Offline validation and outstanding gates

- Run `PYTHONPATH=src python -m unittest discover -s scripts -p 'test_*.py' -v`.
  The new tests use SQLite for deterministic fault injection, **not** as a
  replacement for MySQL 8 service tests.
- Run `PYTHONPATH=src python -m pytest -q --cov=etlguard --cov-branch --cov-fail-under=85`.
- CI must run a genuine MySQL 8.0 and 8.4 service, the full integration suite,
  Ruff and mypy, and the strengthened shadow smoke; all reports must be archived.
- Backup + restore, real-world performance and RPO/RTO checks, rollback exercises,
  monitoring, and all human approvals remain separate gates.

**Release decision:** Neither a passing SQLite demo nor a passing shadow
reconciliation is production authorization. No unattended production scheduler
may be enabled until the real MySQL 8 staging/pilot evidence and independent
change-control approvals are complete.
