"""계약 공통 타입과 버전 상수.

모든 계약은 extra 필드를 금지한다(닫힌 계약). LLM 출력 검증과 JSON Schema
내보내기가 같은 정의를 쓴다. 버전 규칙은 contracts/README.md에 있다.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

CONTRACTS_VERSION = "0.1.0"


class ContractModel(BaseModel):
    """모든 계약 모델의 기반: 알 수 없는 필드를 허용하지 않는다."""

    model_config = ConfigDict(extra="forbid")


class PhysicalType(str, Enum):
    """물리 타입(1계층). docs/PLAN.md 타입 체계 참고."""

    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    BOOLEAN = "boolean"
    STRING = "string"
    DATE = "date"
    TIME = "time"
    TIMESTAMP = "timestamp"
    TIMESTAMPTZ = "timestamptz"
    JSON = "json"
    BINARY = "binary"


class SemanticType(str, Enum):
    """의미 타입(2계층). docs/PLAN.md 의미 타입 목록 참고."""

    IDENTIFIER = "identifier"
    CODE = "code"
    NOMINAL_CATEGORY = "nominal_category"
    ORDINAL_CATEGORY = "ordinal_category"
    CONTINUOUS_NUMBER = "continuous_number"
    DISCRETE_NUMBER = "discrete_number"
    COUNT = "count"
    AMOUNT = "amount"
    RATIO = "ratio"
    DATETIME = "datetime"
    DURATION = "duration"
    COORDINATES = "coordinates"
    ADDRESS = "address"
    FREE_TEXT = "free_text"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    RESIDENT_REGISTRATION_NUMBER = "resident_registration_number"
    BUSINESS_REGISTRATION_NUMBER = "business_registration_number"
    POSTAL_CODE = "postal_code"
    LEGAL_DISTRICT_CODE = "legal_district_code"
    BOOLEAN_LIKE = "boolean_like"
    NESTED_JSON = "nested_json"


class SensitivityLevel(str, Enum):
    """민감도 등급. L3이 가장 제한적이다."""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class Severity(str, Enum):
    """품질 이슈 심각도."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Purpose(str, Enum):
    """업로드 때 고르는 용도. 후보 스키마 채점 가중치가 용도별로 다르다."""

    OLTP = "oltp"
    ANALYTICS = "analytics"
    ARCHIVE = "archive"


class AllowedSchemaOp(str, Enum):
    """S9 후보 보정·S12 선택 수정에서 LLM이 쓸 수 있는 연산."""

    RENAME = "rename"
    CHANGE_TYPE = "change_type"
    SPLIT = "split"
    MERGE = "merge"
    ADD_INDEX = "add_index"


class Evidence(ContractModel):
    """판단의 근거 인용. LLM 산출 계약에서 없는 ID나 모순된 인용은 반려된다."""

    ref: str = Field(description="산출물 ID. 예: 'ColumnProfile:phone'")
    reason: str = Field(description="이 근거가 무엇을 뒷받침하는지")


class UserQuestion(ContractModel):
    """확신도가 낮을 때 만드는 사용자 질문. 비대화 모드에서는 답 없이 기본값 진행."""

    question: str
    context: str = Field(description="질문에 필요한 맥락 요약")
    default_answer: str | None = Field(default=None, description="비대화 모드에서 쓸 기본 답")
