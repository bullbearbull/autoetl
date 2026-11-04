"""S8 의미 해석(SemanticModel) 계약 — LLM 산출물."""

from __future__ import annotations

from pydantic import Field

from autoetl.contracts.common import CONTRACTS_VERSION, ContractModel, Evidence, UserQuestion

STANDARD_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


class ColumnMeaning(ContractModel):
    """컬럼 하나의 의미 해석과 표준 영문 이름."""

    column: str
    meaning: str = Field(description="업무 언어로 쓴 컬럼 의미")
    standard_name: str = Field(
        pattern=STANDARD_NAME_PATTERN, description="표준 영문 이름(소문자 스네이크)"
    )
    evidence: list[Evidence] = Field(min_length=1)


class EntityGroup(ContractModel):
    """FD 기반 엔터티 묶음 결과."""

    name: str = Field(pattern=STANDARD_NAME_PATTERN)
    columns: list[str] = Field(min_length=1)
    rationale: str
    evidence: list[Evidence] = Field(min_length=1)


class SemanticModel(ContractModel):
    """S8 산출물: 의미 해석(LLM 담당).

    컬럼 의미, 엔터티 식별, 표준 영문 이름, 업무 도메인 추정. 모든 판단 항목에
    Evidence 인용이 필수다. 확신이 낮으면 사용자 질문을 만든다.
    """

    contract_version: str = CONTRACTS_VERSION
    business_domain: str = Field(description="추정한 업무 도메인. 예: '전자상거래 주문'")
    column_meanings: list[ColumnMeaning] = Field(min_length=1)
    entities: list[EntityGroup] = Field(default_factory=list)
    unresolved_columns: list[str] = Field(default_factory=list, description="확정하지 못한 컬럼")
    questions: list[UserQuestion] = Field(default_factory=list)
    evidence: list[Evidence] = Field(min_length=1, description="도메인 추정 전체 근거")
