"""S1 샘플링 — SamplingPlan."""

from __future__ import annotations

from autoetl.contracts import SamplingPlan
from autoetl.core.context import AnalysisContext
from autoetl.core.stages import STUB_NOTE


def run(ctx: AnalysisContext) -> SamplingPlan:
    """v0은 항상 전수."""
    return SamplingPlan(
        strategy="full",
        total_rows=ctx.row_count,
        rationale=f"{STUB_NOTE}: 전수 고정",
    )
