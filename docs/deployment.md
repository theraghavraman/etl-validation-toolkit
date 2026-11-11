# Publishing to GitHub and deploying the demo

The v0.1.2 archive contains source code **without internal .git history**. Initialize a repository before publishing (`git init -b main && git add . && git commit -m "Release reviewed v0.1.2"`). A GitHub repository, live Pages URL, CI badges and a public release are only created after you push it into an account you control.

## 1. Publish the repository

Create an empty public GitHub repository named `etl-validation-toolkit` (do not initialize it with another README). From this directory:

```bash
git remote add origin https://github.com/theraghavraman/etl-validation-toolkit.git
git push -u origin main
```

Replace `theraghavraman` with the actual GitHub account before running. Edit `pyproject.toml` `[project.urls]` and README badges/demo link to match that account and commit the changes before pushing. You can alternatively use GitHub CLI (`gh repo create etl-validation-toolkit --public --source=. --remote=origin --push`) if it is installed and authenticated.

## 2. GitHub Pages

Open **Settings → Pages → Build and deployment → Source**, choose **GitHub Actions**. Push to `main`. The `ci.yml` workflow runs checks against a real MySQL 8 service container, generates an up-to-date coverage badge JSON, builds the small browser-safe Python ZIP, and deploys `demo/`. Once it succeeds, the site lives at:

`https://theraghavraman.github.io/etl-validation-toolkit/`

The coverage badge JSON is at `/etl-validation-toolkit/coverage.json` and is consumed by the README Shields endpoint badge. You do not need to manually host a server, database, or API; Pyodide runs locally inside visitor browsers. If Pages actions are restricted by organization policies, enable Pages deployment permissions for the workflow.

## 3. Release v0.1.2

After confirming the full CI suite, commit changes and create the release tag locally (the reviewed archive has no embedded tags):

```bash
git tag v0.1.2
git push origin v0.1.2
```

The `release.yml` workflow then builds Python wheel and source distribution with `python -m build` and creates a GitHub Release with downloadable assets. Tags are immutable release milestones by convention: if you make functionality changes after the v0.1.2 tag, use a later patch release instead of rewriting the published tag. A GitHub Release does not exist before this tag is pushed and the action succeeds.

## 4. Optional PyPI trusted publishing

PyPI publication is intentionally **not enabled by default**. If you want it later, create a PyPI project with a trusted publisher tied to your GitHub repository, workflow and environment, then add an explicitly approved publish job using `pypa/gh-action-pypi-publish`. Keep publishing independent of CI and avoid API tokens in repository secrets whenever a trusted publisher is available.

## 5. Local quality check

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy src/etlguard
pytest --cov=etlguard --cov-branch --cov-report=xml --cov-fail-under=85
python scripts/build_demo_bundle.py
```

The integration tests require a live MySQL 8 database. With Docker Compose, point `ETLGUARD_TEST_MYSQL_URL` to the loopback address and port `3307`, using the local-only demo credentials in `.env.example`; the tests create disposable `it_` tables and clean up afterward. CI uses the MySQL service on port `3306`.

**Promotion gate:** Before tagging or publishing, run and sign off the MySQL 8 integration checks in [the v0.1.2 runbook](production-v0.1.2.md). A wheel build alone does not establish production readiness.
