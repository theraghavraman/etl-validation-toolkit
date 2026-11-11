"""Typer CLI: validate, run, profile, init-db; 0/1/2 exit contract."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from etlguard.config import ConfigError, load_config
from etlguard.pipeline import Pipeline, RunResult
from etlguard.profile import profile_csv

app = typer.Typer(help="ETL Guard: fail bad data before it reaches MySQL.", no_args_is_help=True)
console = Console()


def show_summary(result: RunResult) -> None:
    table = Table(title=f"ETL Guard: {result.pipeline} [{result.status}]")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for label, value in [
        ("Batch ID", result.batch_id), ("Rows read", result.rows_read),
        ("Rows cleaned", result.rows_cleaned), ("Valid", result.rows_valid),
        ("Rejected", result.rows_rejected), ("Duplicates dropped", result.duplicates_dropped),
        ("Loaded", result.rows_loaded), ("Inserted", result.rows_inserted),
        ("Updated/matched", result.rows_updated), ("Duration (s)", result.duration_seconds),
    ]:
        table.add_row(str(label), str(value))
    console.print(table)
    if result.error_counts_by_type:
        console.print("Errors by type:", result.error_counts_by_type)
    if result.error_counts_by_field:
        console.print("Errors by field:", result.error_counts_by_field)
    if result.replayed:
        console.print("[yellow]Already committed: source data was NOT loaded again.[/yellow]")
    if result.warnings:
        console.print("[yellow]Warnings:[/yellow]", "; ".join(result.warnings))
    if result.failures:
        console.print("[red]Failures:[/red]", "; ".join(result.failures))
    console.print(f"Summary: {result.summary_json}")
    console.print(f"Rejects: {result.reject_csv}")


def _execute(path: Path, dry_run: bool, batch_id: str | None = None) -> None:
    try:
        result = Pipeline.from_yaml(path).run(dry_run=dry_run, batch_id=batch_id)
    except (ConfigError, ValueError) as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    show_summary(result)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def validate(config: Annotated[Path, typer.Argument(help="YAML pipeline configuration")]) -> None:
    """Dry run: write rejects + summary; never touch the database."""
    _execute(config, dry_run=True)


@app.command()
def run(
    config: Annotated[Path, typer.Argument(help="YAML pipeline configuration")],
    batch_id: str | None = typer.Option(
        None, "--batch-id", help="Stable UUID for safe retry after an unknown commit outcome"
    ),
) -> None:
    """Validate, stage, transactionally merge and audit a pipeline run."""
    _execute(config, dry_run=False, batch_id=batch_id)


@app.command("profile")
def profile_command(path: Annotated[Path, typer.Argument(help="CSV feed to inspect")]) -> None:
    """Show types, null %, distinct count and min/max by column."""
    try:
        profiles = profile_csv(path)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Profile error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    table = Table(title=f"Column profile: {path.name}")
    for heading in ["Column", "Type", "Rows", "Null %", "Distinct", "Min", "Max"]:
        table.add_column(heading)
    for entry in profiles:
        table.add_row(
            entry["column"], entry["inferred_type"], str(entry["rows"]),
            f"{entry['null_pct']:.2%}", str(entry["distinct"]),
            str(entry["min"]), str(entry["max"]),
        )
    console.print(table)


@app.command("init-db")
def init_db(
    database_url_env: str = typer.Option("ETLGUARD_DATABASE_URL", help="Environment variable for MySQL URL"),
) -> None:
    """Create the durable audit and atomic batch-commit tables, if absent."""
    from etlguard.audit import AuditWriter
    from etlguard.loaders.mysql import MysqlLoader

    url = os.getenv(database_url_env)
    if not url:
        console.print(f"[red]Missing database URL env:[/red] {database_url_env}")
        raise typer.Exit(code=2)
    try:
        loader = MysqlLoader(url)
        AuditWriter(loader.engine).init_db()
        loader.engine.dispose()
    except Exception as exc:
        console.print(f"[red]Database initialization failed:[/red] {type(exc).__name__}")
        raise typer.Exit(code=1) from exc
    console.print("[green]etl_run_audit and etl_batch_commits are ready.[/green]")



@app.command("batch-status")
def batch_status(
    batch_id: str = typer.Argument(..., help="Canonical batch UUID"),
    database_url_env: str = typer.Option("ETLGUARD_DATABASE_URL"),
) -> None:
    """Read the durable outcome before deciding whether to retry a run."""
    from sqlalchemy import inspect, select
    from etlguard.audit import AuditWriter
    from etlguard.loaders.mysql import MysqlLoader

    try:
        if str(UUID(batch_id)) != batch_id:
            raise ValueError("non-canonical UUID")
    except ValueError as exc:
        console.print(f"[red]Invalid batch UUID:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    url = os.getenv(database_url_env)
    if not url:
        console.print(f"[red]Missing database URL env:[/red] {database_url_env}")
        raise typer.Exit(code=2)
    loader = MysqlLoader(url)
    try:
        writer = AuditWriter(loader.engine)
        if not inspect(loader.engine).has_table(writer.commits.name):
            console.print("[yellow]Batch ledger has not been initialized.[/yellow]")
            raise typer.Exit(code=1)
        has_audit = inspect(loader.engine).has_table(writer.table.name)
        with loader.engine.connect() as conn:
            row = conn.execute(select(writer.commits).where(
                writer.commits.c.batch_id == batch_id
            )).mappings().first()
            audit_row = (conn.execute(select(writer.table).where(
                writer.table.c.batch_id == batch_id
            )).mappings().first() if has_audit else None)
        if row is None:
            console.print("[yellow]No v0.1.2 committed load found for this batch ID.[/yellow]")
            if audit_row is not None:
                console.print(f"Audit status: {audit_row['status']}")
                console.print(f"Audit details: {audit_row['error_summary']}")
            console.print("Legacy v0.1.1 loads may have committed without a ledger entry.")
            raise typer.Exit(code=1)
        table = Table(title=f"Committed batch: {batch_id}")
        table.add_column("Field")
        table.add_column("Value")
        for key in ("pipeline", "target_table", "target_mode", "committed_at",
                    "rows_valid", "rows_inserted", "rows_updated", "rows_unchanged",
                    "scd2_versions_created", "request_hash"):
            table.add_row(key, str(row[key]))
        console.print(table)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Status lookup failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        loader.engine.dispose()


@app.command("upgrade-scd2")
def upgrade_scd2(
    config: Annotated[Path, typer.Argument(help="SCD2 YAML pipeline configuration")],
    yes: bool = typer.Option(False, "--yes", help="Confirm maintenance-window schema migration"),
) -> None:
    """Explicitly add the MySQL 8 active-row uniqueness guard to an existing SCD2 table."""
    from etlguard.loaders.mysql import MysqlLoader
    from etlguard.pipeline import resolve_model

    if not yes:
        console.print("[red]Schema migration requires --yes and a database maintenance window.[/red]")
        raise typer.Exit(code=2)
    try:
        settings, _ = load_config(config)
        if settings.target is None or settings.target.mode != "scd2":
            raise ConfigError("The YAML must specify target.mode: scd2")
        url = os.getenv(settings.database_url_env)
        if not url:
            raise ConfigError(f"Set environment variable {settings.database_url_env}")
        model = resolve_model(settings.model)
        loader = MysqlLoader(url)
        try:
            loader.upgrade_scd2(model, settings.target)
        finally:
            loader.engine.dispose()
    except (ConfigError, ValueError) as exc:
        console.print(f"[red]Migration rejected:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        console.print(f"[red]Migration failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc
    console.print("[green]SCD2 active-row uniqueness guard verified.[/green]")

def main() -> None:
    app()


if __name__ == "__main__":
    main()
