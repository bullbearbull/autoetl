"""S7 민감도 분류 — SensitivityMap."""

from __future__ import annotations

from autoetl.contracts import SensitivityMap
from autoetl.core.context import AnalysisContext


def run(ctx: AnalysisContext) -> SensitivityMap:
    """민감 없음 스텁."""
    return SensitivityMap()
