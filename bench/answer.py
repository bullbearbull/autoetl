"""벤치 정답 파일 형식 v0.3 (answer.json).

T-0008 생성기가 내보내던 잠정 형식을 정식화하고, T-0015에서 채점 키를 헤더
문자열에서 컬럼 ID로 바꿨다. T-0020에서 정수 대리 키(kind id·fk)의 정답 물리
타입을 integer로 바로잡았다. 정답은 원본 관계형 스키마 그 자체다: 테이블,
컬럼의 물리 타입, 기본 키, 외래 키, 함수 종속, 컬럼→테이블 배정. 예측(채점
대상)은 같은 구조를 쓰되 컬럼 이름은 채점 대상 변형 CSV의 헤더 문자열이다.
"""

from __future__ import annotations

from pydantic import Field

from autoetl.contracts.common import ContractModel, PhysicalType

ANSWER_VERSION = "0.3.0"

PHYSICAL_TYPE_OF_KIND: dict[str, PhysicalType] = {
    "id": PhysicalType.INTEGER,
    "fk": PhysicalType.INTEGER,
    "int": PhysicalType.INTEGER,
    "amount": PhysicalType.DECIMAL,
    "date": PhysicalType.DATE,
    "code": PhysicalType.STRING,
    "name_kr": PhysicalType.STRING,
    "email": PhysicalType.STRING,
    "phone": PhysicalType.STRING,
    "category": PhysicalType.STRING,
    "text": PhysicalType.STRING,
}
"""생성기 컬럼 종류(kind) → 물리 타입 매핑.

키(id·fk)는 1, 2, 3… 정수 대리 키라 integer다(T-0020). 앞자리 0이 있는 고정
자릿수 코드(kind code)는 문자열 그대로다(0을 보존해야 한다).
"""


class AnswerColumn(ContractModel):
    name: str
    column_id: str = Field(
        description="평면 파일의 컬럼 ID(접두사 없는 이름). FK는 참조 대상 부모 PK의 ID다",
    )
    physical_type: PhysicalType
    is_foreign_key: bool = False


class AnswerForeignKey(ContractModel):
    table: str
    columns: list[str] = Field(min_length=1)
    references_table: str
    references_columns: list[str] = Field(min_length=1)


class AnswerFunctionalDependency(ContractModel):
    table: str
    determinant: list[str] = Field(min_length=1, description="결정자(좌변)")
    dependent: str = Field(description="피결정자(우변)")


class AnswerTable(ContractModel):
    name: str
    primary_key: list[str] = Field(min_length=1)
    columns: list[AnswerColumn] = Field(min_length=1)
    foreign_keys: list[AnswerForeignKey] = Field(default_factory=list)
    functional_dependencies: list[AnswerFunctionalDependency] = Field(
        default_factory=list,
        description="원본에서 성립하는 종속. v0는 PK → 비키 컬럼 형태만 담는다",
    )
    row_count: int = Field(ge=0)


class BenchAnswer(ContractModel):
    """정답 스키마 하나. 파일 이름은 answer.json."""

    answer_version: str = ANSWER_VERSION
    pack: str
    tables: list[AnswerTable] = Field(min_length=1)
    variant_headers: dict[str, list[str]] = Field(
        description="변형 이름 → 그 변형 CSV의 필드 헤더 행(컬럼 순서는 정답과 동일). "
        "채점이 예측 컬럼 이름을 컬럼 ID로 바꾸는 위치 대응에 쓴다",
    )


# ── 예측 형식: 채점에 필요한 최소 구조 ─────────────────────────────────────


class PredictedColumn(ContractModel):
    """예측 컬럼. name은 채점 대상 변형 CSV의 헤더 문자열이다."""

    name: str
    physical_type: PhysicalType
    is_foreign_key: bool = False


class PredictedTable(ContractModel):
    """채점에 필요한 최소 예측. 이름은 채점에서 무시한다(배정은 컬럼 기준)."""

    columns: list[PredictedColumn] = Field(min_length=1)
    primary_key: list[str] = Field(min_length=1)
    foreign_keys: list[AnswerForeignKey] = Field(default_factory=list)
    functional_dependencies: list[AnswerFunctionalDependency] = Field(default_factory=list)


class BenchPrediction(ContractModel):
    """autoetl이 복원한 스키마의 채점용 표현."""

    tables: list[PredictedTable] = Field(min_length=1)
