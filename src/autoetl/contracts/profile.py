"""S2 구조 프로파일(ColumnProfile) 계약."""

from __future__ import annotations

from pydantic import Field, NonNegativeInt

from autoetl.contracts.common import CONTRACTS_VERSION, ContractModel


class LengthStats(ContractModel):
    """문자열 길이 분포. 값은 모두 문자열로 로드된 상태에서 잰다."""

    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)
    mean: float = Field(ge=0)
    p50: float = Field(ge=0)
    p95: float = Field(ge=0)


class PatternMask(ContractModel):
    """형태 마스크와 그 비율. 예: 마스크 '9999-99-99'가 전체의 87%."""

    mask: str = Field(description="'9' 숫자, 'a' 글자, 구두점은 그대로")
    count: NonNegativeInt
    rate: float = Field(ge=0, le=1)


class ColumnProfile(ContractModel):
    """S2 산출물: 컬럼 하나의 구조 프로파일.

    결측률, 고유값 수, 카디널리티 비율, 길이 분포, 형태 마스크 상위 k개를 담는다.
    데이터셋 수준의 중복 행 수는 S6 QualityReport의 유일성 항목에 기록한다.
    """

    contract_version: str = CONTRACTS_VERSION
    column: str
    count: NonNegativeInt = Field(description="검사한 값 수(결측 제외)")
    missing_count: NonNegativeInt
    missing_rate: float = Field(ge=0, le=1)
    unique_count: NonNegativeInt
    cardinality_ratio: float = Field(ge=0, le=1, description="고유값 수 / 값 수")
    length: LengthStats
    top_patterns: list[PatternMask] = Field(
        default_factory=list,
        max_length=10,
        description="형태 마스크 상위 k개(합이 1을 넘지 않음)",
    )
    notes: list[str] = Field(default_factory=list, description="예: '앞자리 0 보존 필요'")
