# v0.1.4 verification

Verified 12 November 2026 in an isolated checkout, using synthetic data and disposable MySQL schemas.

| Check | Result |
|---|---|
| Local runtime | Python 3.13.16 on macOS ARM64 |
| Local database | Loopback-only MySQL 8.4.13, dedicated disposable schemas |
| Application suite | 167 tests passed, zero skipped |
| Branch-aware coverage | 91.39% (minimum required: 85%) |
| Operator tooling | 39 unittest checks passed |
| Ruff and mypy | Passed; all 25 source files type-checked |
| Browser | 14 real Chromium desktop/mobile acceptance checks |
| Installed wheel | 11 CLI/database checks in a fresh environment outside the source checkout |

## Data-integrity regressions

Tests cover over-wide/short/malformed CSV records, duplicate/empty CSV and Excel headers, quoted multiline values and misspelled dataset-check fields. A malformed source cannot reach the loader. Decimal tests verify four places survive an actual MySQL round trip and that a narrower existing target refuses values rather than rounding them.

The existing database tests cover all four load modes, SCD2 history, source/target reconciliation rollback, atomic audit/ledger commits, stable-batch retries, changed-request refusal, uncertain commit recovery and concurrent targets.

## Browser and install acceptance

Real browser checks exercise Customer and Transaction samples, accepted/rejected/JSON views, accepted-row and rejected-row CSV downloads, summary JSON and schema templates. They also check clean CSV uploads, wrong extensions, empty/oversized/malformed files, invalid UTF-8, mobile overflow and recovery after a blocked Python-runtime download. No uncaught JavaScript errors or CSV upload requests are allowed.

The wheel is compared byte-for-byte with the tested Python source, installed into a clean virtual environment, and imported outside the checkout. Acceptance exercises version/help, starter pipeline creation, clean/dirty validation, missing configuration, profiling, database initialization, loading, identical replay and batch status. Source archives are checked for the guide, demo and platform launchers.

## Remote release gates

[CI and Pages](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml) requires quality, browser and installed-package checks before deploying the demo. The [compatibility matrix](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/mysql8-staging-gate.yml) covers MySQL 8.0/8.4 with Python 3.11/3.13, including independent shadow reconciliation and identical retry.

The tagged-release workflow independently reruns both verification and the matrix before publishing downloads. Consult the runs for the exact release commit for remote results. Fresh local and remote evidence is attached to the [release](https://github.com/theraghavraman/etl-validation-toolkit/releases/tag/v0.1.4); earlier reports remain historical.

The checks establish the documented product behavior on synthetic data. They do not measure a customer's actual production volumes or recovery objectives.
