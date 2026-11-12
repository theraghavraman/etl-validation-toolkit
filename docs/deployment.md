# Deployment and release

The source repository is [theraghavraman/etl-validation-toolkit](https://github.com/theraghavraman/etl-validation-toolkit). The browser demo is deployed at [GitHub Pages](https://theraghavraman.github.io/etl-validation-toolkit/).

## GitHub Pages

1. In **Settings → Pages**, select **GitHub Actions** as the deployment source.
2. Set the repository Actions variable `ENABLE_PUBLIC_DEMO_PAGES` to `true`.
3. Push to `main`. The workflow runs lint, type checks and tests against MySQL 8, creates the coverage badge, bundles the Python validation core and publishes `demo/`.

No server or database is required for the deployed demo. Pyodide, Pydantic and SQLite load in the visitor's browser. A CDN connection is needed at startup. User CSV contents stay in the browser.

For local use, run `python3 scripts/serve_demo.py`. After editing shared Python code, rebuild its browser bundle with `python3 scripts/build_demo_bundle.py`.

## Verify a release

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy src/etlguard
python -m unittest discover -s scripts -p 'test_*.py'
pytest --cov=etlguard --cov-branch --cov-report=xml --cov-fail-under=85
python scripts/build_demo_bundle.py
python -m build
```

Supply `ETLGUARD_TEST_MYSQL_URL` for a disposable MySQL schema to run integration tests. GitHub's manual **MySQL 8 Staging Verification** workflow runs Python 3.11/3.13 × MySQL 8.0/8.4, plus shadow/replay checks. Review the complete matrix before tagging.

## Downloadable release

The `release.yml` workflow builds a wheel and source distribution after a version tag is pushed. The tag must match the package version. Release only an exact commit that has passed CI and the staging matrix; do not move a published tag. Attach the manual and verification report as release assets.

For a local installation, download the wheel from GitHub Releases and run `python -m pip install path/to/etl_validation_toolkit-0.1.3-py3-none-any.whl`.

PyPI publishing is not configured. MySQL production rollout is separate from publishing the code and demo; see the operational manual and staging runbook.
