"""S6 품질 진단(QualityReport) 계약."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, NonNegativeInt

from autoetl.contracts.common import CONTRACTS_VERSION, ContractModel, Severity


class QualityDimension(str, Enum):
    COMPLETENESS = "completeness"
    VALIDITY = "validity"
    CONSISTENCY = "consistency"
    UNIQUENESS = "uniqueness"


class QualityIssue(ContractModel):
    """심각도별 이슈 한 건. 형식 혼재 같은 S2 관찰도 여기에 정리된다."""

    id: str = Field(description="이슈 ID. 예: 'Q-0003'. 권고·보고서에서 인용된다")
    dimension: QualityDimension
    severity: Severity
    columns: list[str] = Field(default_factory=list)
    message: str
    count: NonNegativeInt = Field(default=0, description="영향 받은 행 수")
    rate: float | None = Field(default=None, ge=0, le=1)


class DimensionScore(ContractModel):
    score: float = Field(ge=0, le=100)
    detail: str = Field(description="점수 산정 근거 요약")


class QualityReport(ContractModel):
    """S6 산출물: 품질 진단.

    완전성·유효성·일관성·유일성 네 차원 점수와 정제 권고로 이어질 이슈 목록.
    데이터셋 수준의 중복 행 수는 유일성 차원에 기록한다.
    """

    contract_version: str = CONTRACTS_VERSION
    completeness: DimensionScore
    validity: DimensionScore
    consistency: DimensionScore
    uniqueness: DimensionScore
    duplicate_row_count: NonNegativeInt
    issues: list[QualityIssue] = Field(default_factory=list)
