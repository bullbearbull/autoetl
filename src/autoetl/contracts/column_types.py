"""S3 타입 추론(TypeAssignment) 계약."""

from __future__ import annotations

from pydantic import Field

from autoetl.contracts.common import (
    CONTRACTS_VERSION,
    ContractModel,
    Evidence,
    PhysicalType,
    SemanticType,
)


class TypeAlternative(ContractModel):
    """차선 의미 타입 후보와 그 점수."""

    semantic_type: SemanticType
    confidence: float = Field(ge=0, le=1)


class TypeAssignment(ContractModel):
    """S3 산출물: 컬럼 하나의 물리·의미 타입 판정.

    탐지기 신뢰도가 임계치를 넘으면 코드 확정(llm_resolved=false)이고, 모호한 컬럼만
    LLM에 질의한다(llm_resolved=true). LLM 판정에는 Evidence 인용이 필수다.
    decimal 정밀도와 자릿수는 통계에서 온다(소수점이 있는데 INTEGER로 두면 안 된다).
    """

    contract_version: str = CONTRACTS_VERSION
    column: str
    physical_type: PhysicalType
    semantic_type: SemanticType
    precision: int | None = Field(default=None, ge=1, le=38, description="decimal(p,s)의 p")
    scale: int | None = Field(default=None, ge=0, le=38, description="decimal(p,s)의 s")
    detector: str = Field(description="판정한 탐지기 이름. 예: 'regex.phone', 'llm'")
    confidence: float = Field(ge=0, le=1)
    llm_resolved: bool = Field(default=False, description="임계치 미달로 LLM이 판정했는지")
    alternatives: list[TypeAlternative] = Field(default_factory=list, max_length=3)
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="LLM 판정일 때 필수. 예: ColumnProfile의 형태 마스크 인용",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="함정 처리 기록. 예: 'yyyyMMdd 정수 → date', '천 단위 콤마 제거'",
    )
