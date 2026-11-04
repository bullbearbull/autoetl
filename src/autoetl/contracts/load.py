"""S13 적재 플랜(LoadPlan) 계약."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, NonNegativeInt

from autoetl.contracts.common import CONTRACTS_VERSION, ContractModel


class NullHandling(str, Enum):
    EMPTY_STRING = "empty_string"
    NULL = "null"
    DEFAULT_VALUE = "default_value"
    REJECT_ROW = "reject_row"


class IncrementMode(str, Enum):
    APPEND = "append"
    UPSERT = "upsert"
    WATERMARK = "watermark"


class ColumnMapping(ContractModel):
    """원본 컬럼 → 대상 컬럼 변환 규칙 하나."""

    source_column: str
    target_table: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    target_column: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    transform: str = Field(
        default="identity",
        description="변환식. 예: 'strip_comma', 'parse_yyyymmdd', 'identity'",
    )
    type_cast: str = Field(description="예: 'integer', 'decimal(12,2)', 'timestamptz'")
    null_handling: NullHandling
    default_value: str | None = Field(default=None)


class DeduplicationRule(ContractModel):
    enabled: bool
    keys: list[str] = Field(default_factory=list, description="중복 판정 키. 켰을 때만 필수")
    keep: str = Field(default="first", pattern="^(first|last)$")


class LoadOrder(ContractModel):
    """FK 의존 순서를 지키는 테이블 적재 순서."""

    table: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    depends_on: list[str] = Field(default_factory=list)


class ErrorHandling(ContractModel):
    quarantine_table: str = Field(
        default="load_errors", pattern=r"^[a-z][a-z0-9_]*$", description="실패 행 격리 테이블"
    )
    max_error_rate: float = Field(
        default=0.01, ge=0, le=1, description="이 비율을 넘으면 적재 중단"
    )


class LoadPlan(ContractModel):
    """S13 산출물: 적재 플랜(코드 + LLM).

    매핑, 변환 규칙, 적재 순서, 증분 전략, 오류 행 격리를 정의한다. 내보내기는
    ddl.sql, load_plan.yaml, 단독 실행 ETL 스크립트로 한다(P4).
    """

    contract_version: str = CONTRACTS_VERSION
    mappings: list[ColumnMapping] = Field(min_length=1)
    load_order: list[LoadOrder] = Field(min_length=1)
    deduplication: DeduplicationRule
    increment: IncrementMode
    upsert_keys: list[str] = Field(default_factory=list, description="upsert일 때 필수")
    watermark_column: str | None = Field(
        default=None, description="watermark일 때 필수. 예: 'updated_at'"
    )
    error_handling: ErrorHandling = Field(default_factory=ErrorHandling)
    expected_row_count: NonNegativeInt = Field(
        description="정상 적재 시 기대 행 수(정상 행 기준 적재 성공률 판정용)"
    )
