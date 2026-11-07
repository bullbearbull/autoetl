"""S5 관계 분석(RelationGraph) 계약."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, NonNegativeInt

from autoetl.contracts.common import CONTRACTS_VERSION, ContractModel


class CandidateKey(ContractModel):
    """후보 키. 실제 데이터에서 유일성이 확인된 컬럼 조합."""

    columns: list[str] = Field(min_length=1)
    unique: bool
    violation_count: NonNegativeInt = Field(description="유일성 위반 행 수")


class VerificationScope(str, Enum):
    """FD 검증 범위. 전수 GROUP BY로 확정한 것만 full이다(실패 닫힘)."""

    FULL = "full"
    SAMPLE = "sample"


class FunctionalDependency(ContractModel):
    """근사 함수 종속. lhs → rhs가 성립하는 비율과 위반 수를 함께 둔다."""

    lhs: list[str] = Field(
        min_length=1,
        max_length=3,
        description="결정자(좌변). 조합 폭발 제한으로 2~3개 이내",
    )
    rhs: str
    support: float = Field(ge=0, le=1, description="lhs 값 중 rhs가 유일한 비율")
    violation_count: NonNegativeInt
    verification: VerificationScope = Field(
        default=VerificationScope.SAMPLE,
        description="기본은 sample — 전수로 확정한 코드만 full로 적는다(확인하지 "
        "않은 것을 전수로 주장하지 않는다). S9·SOP 재검증 큐가 기계적으로 읽는다.",
    )


class InclusionDependency(ContractModel):
    """포함 종속(외래 키 후보). 단일 데이터셋 안에서도 성립할 수 있다."""

    child_columns: list[str] = Field(min_length=1)
    parent_columns: list[str] = Field(min_length=1)
    inclusion_rate: float = Field(ge=0, le=1, description="child 값이 parent에 포함된 비율")
    parent_table: str | None = Field(default=None, description="다중 입력일 때 부모 테이블명")


class CorrelationMethod(str, Enum):
    PEARSON = "pearson"
    SPEARMAN = "spearman"
    CRAMERS_V = "cramers_v"
    CORRELATION_RATIO = "correlation_ratio"
    MUTUAL_INFO = "mutual_info"


class Correlation(ContractModel):
    a: str
    b: str
    method: CorrelationMethod
    value: float = Field(ge=-1, le=1, description="상호정보량 등 [0,1] 지표는 양수로만 온다")


class Derivation(ContractModel):
    """파생 컬럼 후보. 예: amount = unit_price * quantity."""

    target: str
    sources: list[str] = Field(min_length=1)
    rule_hint: str = Field(description="사람이 확인할 수 있는 규칙 설명")


class Hierarchy(ContractModel):
    """계층 구조. 예: legal_dong_code → city → district."""

    path: list[str] = Field(min_length=2, description="상위 → 하위 순서")


class RelationGraph(ContractModel):
    """S5 산출물: 관계 분석 결과 그래프.

    후보 키, 근사 함수 종속, 포함 종속, 상관, 파생, 계층을 담는다. FD 탐색은 좌변
    크기 제한과 샘플 탐색 후 전수 검증으로 조합 폭발을 제어한다.

    notes는 운영 기록이다 — 작업량 예산 초과 사유, 범위 밖 항목(상호정보량·
    파생·계층), 탐색에서 뺀 컬럼(상수 등). 컬럼 이름·집계 숫자만 담고 셀
    값은 넣지 않는다(P1 공통 규칙).
    """

    contract_version: str = CONTRACTS_VERSION
    candidate_keys: list[CandidateKey] = Field(default_factory=list)
    functional_dependencies: list[FunctionalDependency] = Field(default_factory=list)
    inclusion_dependencies: list[InclusionDependency] = Field(default_factory=list)
    correlations: list[Correlation] = Field(default_factory=list)
    derivations: list[Derivation] = Field(default_factory=list)
    hierarchies: list[Hierarchy] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
