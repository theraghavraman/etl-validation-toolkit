"""ETL Guard public API; lazy imports keep the browser core database-free."""

from typing import Any

__version__ = "0.1.3"
__all__ = ["Pipeline", "RunResult", "__version__"]


def __getattr__(name: str) -> Any:
    if name in {"Pipeline", "RunResult"}:
        from etlguard.pipeline import Pipeline, RunResult

        return {"Pipeline": Pipeline, "RunResult": RunResult}[name]
    raise AttributeError(name)
