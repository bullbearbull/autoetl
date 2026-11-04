"""S7 민감도 분류(SensitivityMap) 계약."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from autoetl.contracts.common import CONTRACTS_VERSION, ContractModel, SensitivityLevel


class SensitiveType(str, Enum):
    """탐지 대상 민감 정보 종류. 체크섬 검증 가능한 종류는 결과에 반영한다."""

    RESIDENT_REGISTRATION_NUMBER = "resident_registration_number"
    FOREIGNER_REGISTRATION_NUMBER = "foreigner_registration_number"
    BUSINESS_REGISTRATION_NUMBER = "business_registration_number"
    CARD_NUMBER = "card_number"
    PHONE_NUMBER = "phone_number"
    EMAIL = "email"
    BANK_ACCOUNT = "bank_account"
    IP_ADDRESS = "ip_address"
    NAME = "name"
    ADDRESS = "address"
    DATE_OF_BIRTH = "date_of_birth"


class SensitivityAssignment(ContractModel):
    column: str
    types: list[SensitiveType] = Field(min_length=1)
    level: SensitivityLevel = Field(description="L0(운영) ~ L3(제한)")
    detector: str
    confidence: float = Field(ge=0, le=1)
    checksum_validated: bool = Field(
        default=False, description="주민번호·카드번호 등 체크섬 검증을 통과했는지"
    )
    matched_rate: float = Field(ge=0, le=1, description="패턴 일치 값의 비율")


class SensitivityMap(ContractModel):
    """S7 산출물: 컬럼별 민감도 지도.

    민감도 등급은 LLM 라우팅, 외부 전송 범위, 로그 기록 수준을 결정하는 기본값이 된다.
    """

    contract_version: str = CONTRACTS_VERSION
    assignments: list[SensitivityAssignment] = Field(
        default_factory=list,
        description="민감으로 판정된 컬럼만 담는다. 나머지는 L0",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="예: '카드번호 Luhn 실패 12건은 위조 가능성'",
    )
