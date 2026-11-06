"""S12 선택 — SelectedSchema."""

from __future__ import annotations

from autoetl.contracts import Purpose, Recommendation, SelectedSchema
from autoetl.core.context import AnalysisContext
from autoetl.core.stages import STUB_NOTE


def run(
    ctx: AnalysisContext, recommendation: Recommendation, *, purpose: Purpose
) -> SelectedSchema:
    """비대화 모드 규칙대로 추천안 자동 선택."""
    return SelectedSchema(
        candidate_id="C1",
        purpose=purpose,
        revalidated=False,
        selection_basis=f"{STUB_NOTE}: 단일 후보 자동 선택",
    )
