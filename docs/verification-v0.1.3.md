# v0.1.3 verification

Verified 12 November 2026 from the supplied v0.1.3 source in an isolated working copy.

## Local results

| Check | Result |
|---|---|
| Runtime | Python 3.13.16 on macOS ARM64 |
| Database | Fresh loopback-only MySQL 8.4.13, disposable schemas |
| pytest | 137 passed, 0 skipped |
| Branch-aware coverage | 91.19% (minimum 85%) |
| Operator verification | 39 unittest checks passed |
| Ruff | Passed |
| mypy | Passed for all 24 source files |
| Browser | 11 acceptance checks passed in real Chromium |
| Installed wheel | 9 CLI checks from outside the source checkout |

The database suite covers all four load modes, SCD2 history, failed reconciliation rollback, atomic audit/ledger commits, identical replay and changed-request refusal. It includes the v0.1.3 regression for concurrent writers to different targets.

The browser checks cover both sample schemas, accepted records and JSON tabs, rejected CSV download, a clean uploaded CSV, wrong file types, empty/oversized/malformed files, recovery after an input error, and mobile overflow. No uncaught page errors or CSV upload requests were observed. The screenshot in the README comes from this run.

The wheel was installed into a separate environment. Checks covered help, clean and dirty validation, invalid config, profiling, database initialization, a first load, identical replay and batch status.

## Continuous verification

[CI and browser acceptance](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/ci.yml) tests MySQL 8.0/Python 3.11 and the browser before deploying Pages. The manual [staging workflow](https://github.com/theraghavraman/etl-validation-toolkit/actions/workflows/mysql8-staging-gate.yml) covers all four combinations of MySQL 8.0/8.4 and Python 3.11/3.13. Check the run for the release commit for its actual result.

Fresh test logs and package/browser reports are attached to the GitHub Release. Earlier packet evidence remains available in the original supplied ZIP and is not substituted for these runs.

## Publishing changes

- Updated v0.1.2 runtime/source to the supplied v0.1.3 fix and concurrency regression.
- Replaced outdated private/handoff README wording with runnable install, CLI, database and demo instructions.
- Added the full manual, a verified screenshot, package links and a browser acceptance gate for Pages.
- Preserved the existing repository history and synthetic fixtures.

The code and demo are suitable for public evaluation. Actual business-database rollout still requires the target-specific pilot and recovery process described by the manual.
