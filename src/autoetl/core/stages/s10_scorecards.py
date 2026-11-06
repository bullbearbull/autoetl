"""S10 검증·채점 — Scorecard 목록."""

from __future__ import annotations

from autoetl.contracts import (
    CriterionScore,
    Purpose,
    SchemaCandidate,
    Scorecard,
    ValidationSummary,
)
from autoetl.core.context import AnalysisContext
from autoetl.core.stages import STUB_NOTE


def _zero_criterion() -> CriterionScore:
    return CriterionScore(score=0.0, detail=STUB_NOTE)


def run(
    ctx: AnalysisContext, candidates: list[SchemaCandidate], *, purpose: Purpose
) -> list[Scorecard]:
    """미실행 0점 스텁."""
    return [
        Scorecard(
            candidate_id=candidate.id,
            integrity=_zero_criterion(),
            redundancy=_zero_criterion(),
            fitness=_zero_criterion(),
            load_simplicity=_zero_criterion(),
            storage_efficiency=_zero_criterion(),
            extensibility=_zero_criterion(),
            security_isolation=_zero_criterion(),
            weighted_total=0.0,
            purpose=purpose,
            validation=ValidationSummary(
                ddl_parse_ok=False,
                trial_load_ok=False,
                constraint_violation_count=0,
                data_loss_rate=0.0,
            ),
        )
        for candidate in candidates
    ]
