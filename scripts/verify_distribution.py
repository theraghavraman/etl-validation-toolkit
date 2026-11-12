"""Build portable release assets and exercise an installed wheel outside the checkout."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import venv
from uuid import uuid4
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--report", type=Path, default=ROOT / "verification-evidence/package.json")
    args = parser.parse_args()
    dist = args.dist.resolve()
    wheel = next(dist.glob("*.whl"))
    source = next(dist.glob("*.tar.gz"))
    with ZipFile(wheel) as archive:
        for path in archive.namelist():
            if path.startswith("etlguard/") and path.endswith(".py"):
                assert archive.read(path) == (ROOT / "src" / path).read_bytes(), path
    with tarfile.open(source) as archive:
        members = archive.getmembers()
        for required in ("README.md", "docs/USER_GUIDE.md", "demo/app.js",
                         "scripts/serve_demo.py", "Launch ETL Guard.cmd", "Launch ETL Guard.sh"):
            assert any(m.name.endswith("/" + required) for m in members), required
        zip_path = dist / source.name.replace(".tar.gz", "-source.zip")
        with ZipFile(zip_path, "w", ZIP_DEFLATED) as portable:
            for member in members:
                if member.isfile():
                    data = archive.extractfile(member)
                    assert data is not None
                    entry = ZipInfo(member.name)
                    entry.external_attr = (member.mode | 0o100000) << 16
                    entry.compress_type = ZIP_DEFLATED
                    portable.writestr(entry, data.read())
    checks = []
    with tempfile.TemporaryDirectory(prefix="etlguard-install-") as temporary:
        work = Path(temporary)
        environment = work / "environment"
        venv.EnvBuilder(with_pip=True).create(environment)
        binary = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binary / ("python.exe" if os.name == "nt" else "python")
        cli = binary / ("etlguard.exe" if os.name == "nt" else "etlguard")
        env = {k: v for k, v in os.environ.items()
               if k != "PYTHONPATH" and not k.startswith("ETLGUARD_")}
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
                       cwd=work, env=env, check=True, stdout=subprocess.DEVNULL, timeout=300)
        location = subprocess.check_output([str(python), "-c", "import etlguard;print(etlguard.__file__)"],
                                           cwd=work, env=env, text=True).strip()
        assert str(environment) in location, "Wheel must import outside the checkout"

        def run(name: str, command: list[str], expected: int = 0, contains: str | None = None) -> None:
            process = subprocess.run([str(cli), *command], cwd=work, env=env,
                                     capture_output=True, text=True, timeout=60)
            assert process.returncode == expected, (name, process.stdout, process.stderr)
            if contains:
                assert contains in process.stdout, name
            checks.append({"name": name, "exit_code": process.returncode, "status": "passed"})

        run("help", ["--help"], contains="init")
        run("version", ["--version"], contains="ETLGuard")
        run("create example", ["init", "sample"])
        config = work / "sample/pipeline.yaml"
        run("clean validation", ["validate", str(config)])
        dirty = config.with_name("dirty.yaml")
        dirty.write_text(config.read_text().replace("data/customers_clean.csv", "data/customers_messy.csv"))
        run("dirty validation", ["validate", str(dirty)], expected=1)
        run("missing configuration", ["validate", "missing.yaml"], expected=2)
        run("profile", ["profile", str(work / "sample/data/customers_clean.csv")])
        url = os.getenv("ETLGUARD_TEST_MYSQL_URL")
        if url:
            env["ETLGUARD_DATABASE_URL"] = url
            run("database initialization", ["init-db"])
            # Each check uses a disposable test table and stable batch UUID.
            config.write_text(config.read_text().replace("table: customers_clean", "table: it_pkg_" + uuid4().hex[:10]))
            batch = str(uuid4())
            run("database load", ["run", str(config), "--batch-id", batch])
            run("identical replay", ["run", str(config), "--batch-id", batch], contains="Already committed")
            run("batch status", ["batch-status", batch], contains="Committed batch")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"status": "passed", "installed_outside_checkout": True,
                                       "checks": checks}, indent=2) + "\n")
    for name in ("USER_GUIDE.md",):
        shutil.copy2(ROOT / "docs" / name, dist / name)
    sums = [p for p in sorted(dist.iterdir()) if p.is_file() and p.name != "SHA256SUMS"]
    (dist / "SHA256SUMS").write_text("".join(sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
                                             for p in sums))
    print(f"Installed-wheel acceptance: {len(checks)} checks passed")


if __name__ == "__main__":
    main()
