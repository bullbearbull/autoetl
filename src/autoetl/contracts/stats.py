"""S4 타입별 심화 통계(ColumnStats) 계약."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, NonNegativeInt

from autoetl.contracts.common import CONTRACTS_VERSION, ContractModel


class OutlierMethod(str, Enum):
    IQR = "iqr"
    ISOLATION_FOREST = "isolation_forest"


class OutlierSummary(ContractModel):
    method: OutlierMethod
    count: NonNegativeInt
    rate: float = Field(ge=0, le=1)
    bounds: str = Field(description="예: 'IQR 1.5배: [12.0, 980.0]'")


class NumericStats(ContractModel):
    """수치 컬럼 분포와 정밀도."""

    mean: float
    std: float
    minimum: float
    maximum: float
    p25: float
    p50: float
    p75: float
    skew: float
    max_decimal_digits: NonNegativeInt = Field(description="정밀도 추정용 최대 소수 자릿수")
    outliers: OutlierSummary


class CategoryValue(ContractModel):
    """범주 값과 비율. Secure 모드에서 외부 전송 시 토큰(CAT_01)으로 바뀐다."""

    value: str
    count: NonNegativeInt
    rate: float = Field(ge=0, le=1)


class CategoricalStats(ContractModel):
    entropy: float = Field(ge=0, description="섀넌 엔트로피(비트)")
    top_values: list[CategoryValue] = Field(default_factory=list, max_length=20)
    distinct_ratio: float = Field(ge=0, le=1)


class GapSummary(ContractModel):
    """시계열 결측 구간."""

    count: NonNegativeInt
    longest: str = Field(description="가장 긴 구간 표현. 예: '2026-03-01 ~ 2026-04-11'")


class SeasonalitySummary(ContractModel):
    detected: bool
    period: str | None = Field(default=None, description="예: '7d', '12mo'")
    method: str = Field(default="acf", pattern="^(stl|acf)$")


class TemporalStats(ContractModel):
    minimum: str = Field(description="ISO 8601")
    maximum: str = Field(description="ISO 8601")
    dominant_frequency: str | None = Field(default=None, description="예: '1D', '1h'")
    gaps: GapSummary
    seasonality: SeasonalitySummary


class StatsKind(str, Enum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    DATETIME = "datetime"
    TEXT = "text"
    OTHER = "other"


class ColumnStats(ContractModel):
    """S4 산출물: 컬럼 하나의 심화 통계.

    종류(kind)에 따라 해당하는 하위 통계만 채운다. 나머지는 None으로 둔다.
    개별 값이 드러나는 최솟값·최댓값은 Secure 모드에서 외부로 나가지 않는다.
    """

    contract_version: str = CONTRACTS_VERSION
    column: str
    kind: StatsKind
    numeric: NumericStats | None = None
    categorical: CategoricalStats | None = None
    temporal: TemporalStats | None = None
