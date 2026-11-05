"""필수 관문과 최소·권장 모델 판정 기준 v0.

docs/PLAN.md 벤치 절의 초기 제안값을 그대로 코드화한다. 수치 변경은 소유자
검토 대상이다(AGENTS.md 3절 — 벤치 판정 기준과 최소·권장 모델).
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from autoetl.contracts.common import ContractModel

REQUIRED_DDL_SUCCESS_RATE = 1.0
"""필수 관문: 시험 적재·DDL 성공률 100%."""

REQUIRED_LEAK_COUNT = 0
"""필수 관문: Secure 모드 카나리 유출 0건."""

REQUIRED_JSON_COMPLIANCE = 0.99
"""필수 관문: JSON 준수율 99% 이상."""

MINIMUM_MODEL_RATIO = 0.85
"""최소 모델: 필수 관문 통과 + 최고 점수의 85% 이상인 것 중 가장 작은 모델."""

RECOMMENDED_MODEL_RATIO = 0.95
"""권장 모델: 최고 점수의 95% 이상인 것 중 비용·자원 효율이 가장 좋은 모델."""


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


class RunMetrics(ContractModel):
    """모델 하나의 벤치 실행 결과 요약. 실행기(런너)가 채운다."""

    model: str = Field(description="모델 식별자(예: 'qwen3-8b')")
    parameters_b: float = Field(ge=0, description="전체 파라미터 수(십억 단위)")
    score: float = Field(ge=0, le=100, description="종합 점수(schema_recovery_score 포함)")
    ddl_success_rate: float = Field(ge=0, le=1)
    leak_count: int = Field(ge=0)
    json_compliance: float = Field(ge=0, le=1)
    relative_cost: float = Field(
        default=1.0,
        gt=0,
        description="비용·자원 상대 지표(작을수록 좋다. 기준 모델=1.0). "
        "0은 허용하지 않는다(효율 나눗셈 정의를 위해)",
    )


class GateResult(ContractModel):
    model: str
    ddl: GateStatus
    leak: GateStatus
    json_compliance_gate: GateStatus
    passed: bool

    def summary(self) -> str:
        return (
            f"{self.model}: ddl={self.ddl.value} leak={self.leak.value} "
            f"json={self.json_compliance_gate.value} → {'통과' if self.passed else '실패'}"
        )


def check_gates(metrics: RunMetrics) -> GateResult:
    """필수 관문 검사. 세 항목을 모두 넘겨야 통과다."""
    ddl = (
        GateStatus.PASSED
        if metrics.ddl_success_rate >= REQUIRED_DDL_SUCCESS_RATE
        else GateStatus.FAILED
    )
    leak = GateStatus.PASSED if metrics.leak_count <= REQUIRED_LEAK_COUNT else GateStatus.FAILED
    js = (
        GateStatus.PASSED
        if metrics.json_compliance >= REQUIRED_JSON_COMPLIANCE
        else GateStatus.FAILED
    )
    return GateResult(
        model=metrics.model,
        ddl=ddl,
        leak=leak,
        json_compliance_gate=js,
        passed=ddl is GateStatus.PASSED and leak is GateStatus.PASSED and js is GateStatus.PASSED,
    )


class ModelClassification(ContractModel):
    minimum_model: str | None = Field(
        description="관문 통과 + 최고 점수의 85% 이상 중 가장 작은 모델"
    )
    recommended_model: str | None = Field(
        description="최고 점수의 95% 이상 중 효율(점수/비용)이 가장 좋은 모델"
    )
    best_score: float
    reason: str = Field(description="판정 근거 요약")


def classify_models(runs: list[RunMetrics]) -> ModelClassification:
    """최소·권장 모델을 판정한다.

    - 기준선(best_score)은 전체 모델 가운데 최고 점수로 잡는다. 관문을 통과하지
      못한 프런티어 모델이 최고점이라면 그 점수가 기준선이 된다(PLAN의 '최고 모델
      점수의 85% 이상' 해석). 후보(판정 대상)는 관문을 통과한 모델만이다.
    - 최소: 기준선 x 0.85 이상인 후보 중 파라미터가 가장 작은 것.
    - 권장: 기준선 x 0.95 이상인 후보 중 점수/비용 지표가 가장 높은 것.
    - 관문을 통과한 모델이 없거나 기준선에 닿는 후보가 없으면 None(판정 보류).
    - 기준선이 0 이하면 판정을 보류한다 — 0 >= 0 x 0.85가 성립해 최소·권장이
      의미 없이 지정되는 일을 막는다(T-0016).
    """
    if not runs:
        return ModelClassification(
            minimum_model=None,
            recommended_model=None,
            best_score=0.0,
            reason="실행 결과가 없다",
        )
    best_score = max(run.score for run in runs)
    if best_score <= 0:
        return ModelClassification(
            minimum_model=None,
            recommended_model=None,
            best_score=best_score,
            reason=f"전체 최고 점수(기준선)가 {best_score:.1f}점으로 0 이하라 판정을 보류한다",
        )
    qualified = [run for run in runs if check_gates(run).passed]
    if not qualified:
        return ModelClassification(
            minimum_model=None,
            recommended_model=None,
            best_score=best_score,
            reason="필수 관문을 통과한 모델이 없다",
        )
    minimum_candidates = [run for run in qualified if run.score >= best_score * MINIMUM_MODEL_RATIO]
    minimum = (
        min(minimum_candidates, key=lambda run: run.parameters_b) if minimum_candidates else None
    )
    recommended_candidates = [
        run for run in qualified if run.score >= best_score * RECOMMENDED_MODEL_RATIO
    ]
    recommended = (
        max(recommended_candidates, key=lambda run: run.score / run.relative_cost)
        if recommended_candidates
        else None
    )
    reason = (
        f"통과 {len(qualified)}/{len(runs)}모델, 전체 최고 점수(기준선) {best_score:.1f}. "
        f"최소 기준선 {best_score * MINIMUM_MODEL_RATIO:.1f}점, "
        f"권장 기준선 {best_score * RECOMMENDED_MODEL_RATIO:.1f}점."
    )
    return ModelClassification(
        minimum_model=minimum.model if minimum else None,
        recommended_model=recommended.model if recommended else None,
        best_score=best_score,
        reason=reason,
    )
