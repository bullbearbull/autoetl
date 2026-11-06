"""S6 품질 진단 — QualityReport."""

from __future__ import annotations

from autoetl.contracts import DimensionScore, QualityReport
from autoetl.core.context import AnalysisContext
from autoetl.core.stages import STUB_NOTE


def run(ctx: AnalysisContext) -> QualityReport:
    """0점 스텁."""
    return QualityReport(
        completeness=DimensionScore(score=0.0, detail=STUB_NOTE),
        validity=DimensionScore(score=0.0, detail=STUB_NOTE),
        consistency=DimensionScore(score=0.0, detail=STUB_NOTE),
        uniqueness=DimensionScore(score=0.0, detail=STUB_NOTE),
        duplicate_row_count=0,
    )
