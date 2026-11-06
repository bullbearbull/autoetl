"""S5 관계 분석 — RelationGraph."""

from __future__ import annotations

from autoetl.contracts import RelationGraph
from autoetl.core.context import AnalysisContext


def run(ctx: AnalysisContext) -> RelationGraph:
    """빈 그래프 스텁."""
    return RelationGraph()
