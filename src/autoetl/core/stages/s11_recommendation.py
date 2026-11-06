"""S11 권고 — Recommendation."""

from __future__ import annotations

from autoetl.contracts import Evidence, RankedCandidate, Recommendation, SchemaCandidate
from autoetl.core.context import AnalysisContext
from autoetl.core.stages import STUB_NOTE


def run(ctx: AnalysisContext, candidates: list[SchemaCandidate]) -> Recommendation:
    """단일 후보 자동 추천 스텁."""
    return Recommendation(
        ranking=[
            RankedCandidate(
                candidate_id="C1",
                rank=1,
                reasons=[Evidence(ref="Scorecard:C1", reason=STUB_NOTE)],
            )
        ],
        recommended_id="C1",
        tradeoffs=f"{STUB_NOTE}: 비교 대상 없음",
    )
