"""S9 후보 스키마, S10 채점, S11 권고, S12 선택 계약."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, NonNegativeInt

from autoetl.contracts.common import (
    CONTRACTS_VERSION,
    AllowedSchemaOp,
    ContractModel,
    Evidence,
    PhysicalType,
    Purpose,
)


class SchemaPattern(str, Enum):
    """후보 생성 전략."""

    THIRD_NORMAL_FORM = "3nf"
    STAR = "star"
    WIDE = "wide"
    TIMESERIES = "timeseries"
    HYBRID_JSONB = "hybrid_jsonb"


class TargetColumn(ContractModel):
    """대상 테이블 컬럼 정의."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    physical_type: PhysicalType
    precision: int | None = Field(default=None, ge=1, le=38)
    scale: int | None = Field(default=None, ge=0, le=38)
    source_columns: list[str] = Field(min_length=1, description="원본 컬럼(들)")
    nullable: bool
    is_primary_key: bool = Field(default=False)
    foreign_key: str | None = Field(default=None, description="'다른테이블(컬럼)' 형식의 참조")
    description: str = Field(default="")


class TargetTable(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    columns: list[TargetColumn] = Field(min_length=1)
    partition: str | None = Field(default=None, description="시계열 파티셔닝 정의")
    index: list[str] = Field(default_factory=list, description="추가 인덱스 컬럼 목록")
    is_sensitive_isolated: bool = Field(
        default=False, description="민감 컬럼 격리를 위해 분리한 테이블인지"
    )


class SchemaCandidate(ContractModel):
    """S9 산출물: 후보 스키마 하나. 코드가 만들고 LLM은 허용 연산으로만 보정한다."""

    contract_version: str = CONTRACTS_VERSION
    id: str = Field(description="후보 ID. 예: 'C1'. 채점·권고에서 인용")
    pattern: SchemaPattern
    tables: list[TargetTable] = Field(min_length=1)
    rationale: str = Field(description="이 전략을 만든 이유")
    source: str = Field(default="code", pattern="^(code|code\\+llm)$", description="생성 주체")


class ValidationSummary(ContractModel):
    """S10 검증 결과. 임시 스키마 시험 적재까지 마친 결과만 채운다."""

    ddl_parse_ok: bool
    trial_load_ok: bool
    constraint_violation_count: NonNegativeInt
    data_loss_rate: float = Field(ge=0, le=1, description="원본 대비 손실 비율")


class CriterionScore(ContractModel):
    score: float = Field(ge=0, le=100)
    detail: str


class Scorecard(ContractModel):
    """S10 산출물: 후보 하나의 평가 기준표 점수.

    항목별 0~100점을 매기고 용도(Purpose)별 가중합을 계산한다. 같은 데이터라도
    용도가 다르면 정답 스키마가 달라지기 때문이다.
    """

    contract_version: str = CONTRACTS_VERSION
    candidate_id: str
    integrity: CriterionScore = Field(description="무결성: 제약 범위, FD 보존, 무손실 조인")
    redundancy: CriterionScore = Field(description="중복도")
    fitness: CriterionScore = Field(description="용도 적합성")
    load_simplicity: CriterionScore = Field(description="적재 단순성")
    storage_efficiency: CriterionScore = Field(description="저장 효율")
    extensibility: CriterionScore = Field(description="확장 용이성")
    security_isolation: CriterionScore = Field(description="민감 컬럼 격리")
    weighted_total: float = Field(ge=0, le=100, description="용도별 가중합 점수")
    purpose: Purpose
    validation: ValidationSummary


class RankedCandidate(ContractModel):
    candidate_id: str
    rank: NonNegativeInt
    reasons: list[Evidence] = Field(min_length=1, description="점수표 항목을 인용한 사유")


class Recommendation(ContractModel):
    """S11 산출물: 권고(LLM 담당). 순위와 사유에 근거 ID 인용이 필수다."""

    contract_version: str = CONTRACTS_VERSION
    ranking: list[RankedCandidate] = Field(min_length=1)
    recommended_id: str
    tradeoffs: str = Field(description="후보 간 장단점 요약")
    caution: str = Field(default="", description="선택 전 확인할 사항")


class SchemaModification(ContractModel):
    """S12 사용자·LLM 수정 한 건. 허용 연산만 쓸 수 있다."""

    op: AllowedSchemaOp
    target: str = Field(description="연산 대상. 예: 'customers.phone'")
    args: dict[str, str] = Field(default_factory=dict, description="연산별 인자")
    reason: str = Field(default="")


class SelectedSchema(ContractModel):
    """S12 산출물: 사용자가 고른 후보와 수정. 수정 후 재검증을 거친다."""

    contract_version: str = CONTRACTS_VERSION
    candidate_id: str
    purpose: Purpose
    modifications: list[SchemaModification] = Field(default_factory=list)
    revalidated: bool = Field(description="수정 반영 후 재검증 통과 여부")
    selection_basis: str = Field(default="", description="사용자가 남긴 선택 이유")
