"""Strictly validated YAML config, with paths resolved relative to the YAML file."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(ValueError):
    """Invalid user-supplied pipeline configuration (CLI exit status 2)."""


class StrictSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaginationSettings(StrictSettings):
    mode: Literal["none", "page", "offset", "next"] = "none"
    page_param: str = "page"
    offset_param: str = "offset"
    limit_param: str = "limit"
    page_size: int = Field(default=100, ge=1)
    start: int = Field(default=1, ge=0)
    max_pages: int = Field(default=100, ge=1)
    next_path: str = "links.next"


class SourceSettings(StrictSettings):
    type: Literal["csv", "excel", "api"]
    name: str | None = None
    path: str | None = None
    url: str | None = None
    delimiter: str = ","
    encoding: str = "utf-8-sig"
    header_row: int = Field(default=1, ge=1)
    chunksize: int = Field(default=10000, ge=1)
    sheet: str | int = 0
    skip_rows: int = Field(default=0, ge=0)
    records_path: str = ""
    bearer_token_env: str | None = None
    timeout_seconds: float = Field(default=15, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_seconds: float = Field(default=0.5, ge=0)
    pagination: PaginationSettings = Field(default_factory=PaginationSettings)

    @model_validator(mode="after")
    def check_location(self) -> SourceSettings:
        if self.type in {"csv", "excel"} and not self.path:
            raise ValueError("CSV/Excel sources require path")
        if self.type == "api" and not self.url:
            raise ValueError("API sources require url")
        if len(self.delimiter) != 1 or self.delimiter in {"\r", "\n", '"'}:
            raise ValueError("delimiter must be one character, excluding newline and quote")
        return self


class CleaningSettings(StrictSettings):
    trim: bool = True
    snake_case_columns: bool = True
    null_tokens: list[str] = Field(default_factory=lambda: ["", "NA", "N/A", "null", "-"])
    rename_map: dict[str, str] = Field(default_factory=dict)
    date_columns: list[str] = Field(default_factory=list)
    date_formats: list[str] = Field(default_factory=lambda: [
        "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y.%m.%d"
    ])
    number_columns: list[str] = Field(default_factory=list)
    lowercase: list[str] = Field(default_factory=list)
    uppercase: list[str] = Field(default_factory=list)
    drop_duplicates: bool = True


class ForeignKeySettings(StrictSettings):
    values: list[str | int] | None = None
    path: str | None = None
    column: str | None = None

    @model_validator(mode="after")
    def check_reference(self) -> ForeignKeySettings:
        if (self.values is None) == (self.path is None):
            raise ValueError("foreign key reference requires exactly one of values or path")
        if self.path is not None and not self.column:
            raise ValueError("file-based foreign key reference requires column")
        return self


class RowCountSettings(StrictSettings):
    min: int | None = Field(default=None, ge=0)
    max: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def check_bounds(self) -> RowCountSettings:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("expected_row_count min cannot exceed max")
        return self


class ChecksSettings(StrictSettings):
    unique: list[str] = Field(default_factory=list)
    foreign_keys: dict[str, ForeignKeySettings] = Field(default_factory=dict)
    expected_row_count: RowCountSettings | None = None
    max_null_pct: dict[str, float] = Field(default_factory=dict)

    @field_validator("max_null_pct")
    @classmethod
    def check_null_pct(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not 0 <= pct <= 1 for pct in value.values()):
            raise ValueError("max_null_pct must be between 0 and 1")
        return value


class TargetSettings(StrictSettings):
    table: str
    mode: Literal["append", "truncate_insert", "upsert", "scd2"] = "append"
    key: list[str] = Field(default_factory=list)
    tracked_columns: list[str] = Field(default_factory=list)
    batch_size: int = Field(default=500, ge=1)
    lock_timeout_seconds: int = Field(default=30, ge=0, le=300)
    min_replace_ratio: float | None = Field(default=None, ge=0, le=1)

    @field_validator("table")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        if not IDENTIFIER.fullmatch(value) or len(value) > 48:
            raise ValueError("table must be a SQL identifier of at most 48 characters")
        if value.casefold().startswith("stg_") or value.casefold() in {
            "etl_run_audit", "etl_batch_commits",
        }:
            raise ValueError("table is a reserved ETL Guard name")
        return value

    @model_validator(mode="after")
    def check_mode(self) -> TargetSettings:
        if self.mode in {"upsert", "scd2"} and not self.key:
            raise ValueError("upsert/scd2 needs at least one business key")
        if len(set(self.key)) != len(self.key):
            raise ValueError("target.key must not contain duplicate column names")
        for name in [*self.key, *self.tracked_columns]:
            if not IDENTIFIER.fullmatch(name):
                raise ValueError(f"invalid target column identifier: {name}")
        return self


class PolicySettings(StrictSettings):
    fail_if_reject_rate_above: float | None = Field(default=None, ge=0, le=1)
    # Replacement mode is destructive: both overrides require explicit opt-in.
    allow_partial_replace: bool = False
    allow_empty_replace: bool = False


class OutputSettings(StrictSettings):
    directory: str = "reports"


class PipelineSettings(StrictSettings):
    pipeline: str
    source: SourceSettings
    model: str
    cleaning: CleaningSettings = Field(default_factory=CleaningSettings)
    checks: ChecksSettings = Field(default_factory=ChecksSettings)
    target: TargetSettings | None = None
    policy: PolicySettings = Field(default_factory=PolicySettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    database_url_env: str = "ETLGUARD_DATABASE_URL"

    @field_validator("pipeline")
    @classmethod
    def safe_pipeline_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
            raise ValueError("pipeline must be a 1-128 character filename-safe identifier")
        return value


def load_config(path: str | Path) -> tuple[PipelineSettings, Path]:
    file_path = Path(path).expanduser().resolve()
    import yaml

    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ConfigError("YAML must contain a mapping")
        settings = PipelineSettings.model_validate(data)
        return settings, file_path.parent
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ConfigError(f"Invalid config {file_path}: {exc}") from exc
