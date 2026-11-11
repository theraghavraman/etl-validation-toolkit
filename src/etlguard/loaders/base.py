"""Target loader contract."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from etlguard.config import TargetSettings
from etlguard.reconcile import LoadCounts
from etlguard.validation import Candidate


class Loader(Protocol):
    def load(
        self, candidates: list[Candidate], batch_id: str,
        model: type[BaseModel], target: TargetSettings,
    ) -> LoadCounts: ...
