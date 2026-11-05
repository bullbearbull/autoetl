"""데이터·로그 등급 정책 v0의 기계 판독형 정의.

정책 문서는 docs/policy/data-log-levels.md이고 이 모듈은 그 요점을 코드가 쓸 수
있게 내려놓은 것이다. 규칙 변경은 소유자 검토 대상이다(AGENTS.md 3절). 실제 강제
(로그 가림 처리기, 전송 차단 장치, 카나리 실행)는 P2 이후 이 정의를 따라 만든다.

강제 기준 표이므로 읽기 전용이다. 모델은 frozen이고 표는 MappingProxyType·tuple로
노출한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType

from pydantic import ConfigDict, Field

from autoetl.contracts.common import ContractModel, SensitivityLevel
from autoetl.contracts.sensitivity import SensitiveType

CANARY_PREFIX = "CANARY-"
"""카나리 테스트에서 심는 표식 접두사. 값은 실행마다 랜덤(UUID)으로 만든다."""


class SecureMode(str, Enum):
    """외부 LLM 전송 모드(docs/PLAN.md Secure Mode 절)."""

    OFF = "off"
    SECURE = "secure"
    STRICT = "strict"


DEFAULT_SECURE_MODE = SecureMode.SECURE
"""기본 모드. PLAN의 LLM 설정(화면 7)과 SDK 예시(secure=True)와 같다."""


class LogLevel(str, Enum):
    """로그 등급. 높아질수록 기록이 제한된다.

    데이터 등급(SensitivityLevel)과 값 표기(L0~L3)가 같아 혼동되기 쉽다. 셀 값의
    로그 등급을 정할 때는 log_level_for_value()만 쓴다.
    """

    OPERATIONAL = "L0"  # 운영
    INTERNAL = "L1"  # 내부
    CONFIDENTIAL = "L2"  # 기밀
    RESTRICTED = "L3"  # 제한


class FrozenPolicy(ContractModel):
    """강제 기준이 되는 정책 모델. 실행 중 수정 불가."""

    model_config = ConfigDict(frozen=True)


class LogPolicy(FrozenPolicy):
    """로그 등급 하나의 기록·보존 규칙."""

    level: LogLevel
    name: str = Field(description="예: '운영'")
    contains: str = Field(description="이 등급 정보에 담길 수 있는 것")
    may_record: bool = Field(description="로그·저장 매체에 기록 가능한지")
    requires_explicit_debug: bool = Field(
        default=False, description="기록하려면 사용자가 디버그를 직접 켜야 하는지"
    )
    encrypted_at_rest: bool = Field(default=False, description="보존 시 암호화 필수")
    retention: str = Field(description="보존 규칙 요약(사람용)")
    retention_hours: int | None = Field(
        default=None, ge=1, description="보존 시간(시간 단위). None은 기록 자체가 없음"
    )
    retention_configurable: bool = Field(
        default=False, description="사용자가 보존 기한을 바꿀 수 있는지"
    )
    audit_alternative: str = Field(description="기록 대신 감사에 남길 수 있는 것", default="")


LOG_LEVEL_POLICY: Mapping[LogLevel, LogPolicy] = MappingProxyType(
    {
        LogLevel.OPERATIONAL: LogPolicy(
            level=LogLevel.OPERATIONAL,
            name="운영",
            contains="작업 상태, 단계 진행, 오류 코드, 성능 지표. 데이터 값 없음",
            may_record=True,
            retention="기본 보존(30일), 평문 허용",
            retention_hours=24 * 30,
            retention_configurable=True,
            audit_alternative="그대로 기록",
        ),
        LogLevel.INTERNAL: LogPolicy(
            level=LogLevel.INTERNAL,
            name="내부",
            contains=(
                "컬럼명, 집계 통계(비율·개수·엔트로피·형태 마스크 분포), 품질 이슈 유형. "
                "개별 값이 드러나는 통계(min/max·top_values)는 담지 않는다"
            ),
            may_record=True,
            retention="기본 보존(30일)",
            retention_hours=24 * 30,
            retention_configurable=True,
            audit_alternative="그대로 기록",
        ),
        LogLevel.CONFIDENTIAL: LogPolicy(
            level=LogLevel.CONFIDENTIAL,
            name="기밀",
            contains=(
                "셀 값(데이터 L0·L1 원문, 또는 가림 처리를 거친 모든 값), "
                "데이터가 포함된 프롬프트·응답"
            ),
            may_record=True,
            requires_explicit_debug=True,
            encrypted_at_rest=True,
            retention="디버그 직접 활성화 시에만 암호화 기록, 24시간 후 자동 삭제",
            retention_hours=24,
            audit_alternative="이벤트 유형과 크기만(값 단위 해시는 금지)",
        ),
        LogLevel.RESTRICTED: LogPolicy(
            level=LogLevel.RESTRICTED,
            name="제한",
            contains=(
                "데이터 L2·L3 컬럼의 원문 값(전화번호·이메일·이름·주소·계좌·"
                "주민등록번호·카드번호 등). 비밀 키·API 키"
            ),
            may_record=False,
            retention="원문은 기록 금지. 가림 처리(형태 마스크 등)를 거치면 L2로 기록 가능",
            retention_hours=None,
            audit_alternative="존재 사실·종류·건수만(값 단위 해시는 금지)",
        ),
    }
)

DATA_RAW_VALUE_LOG_LEVEL: Mapping[SensitivityLevel, LogLevel] = MappingProxyType(
    {
        SensitivityLevel.L0: LogLevel.CONFIDENTIAL,
        SensitivityLevel.L1: LogLevel.CONFIDENTIAL,
        SensitivityLevel.L2: LogLevel.RESTRICTED,
        SensitivityLevel.L3: LogLevel.RESTRICTED,
    }
)
"""셀 원문 값을 로그에 담을 때의 등급(가림 처리 전).

어떤 원시 셀 값이든 로그에 담기면 최소 L2다. 데이터 L2·L3 컬럼의 원문은 L3이다.
"""


def log_level_for_value(data_level: SensitivityLevel, *, masked: bool) -> LogLevel:
    """셀 값 하나를 기록할 때 적용할 로그 등급.

    - 데이터 L0·L1 원문: 로그 L2(디버그 직접 활성화 + 암호화 조건).
    - 데이터 L2·L3 원문: 로그 L3(원문 기록 금지).
    - 가림 처리(마스킹·형태 마스크·구간화)를 거친 값: 로그 L2.
    """
    if masked or DATA_RAW_VALUE_LOG_LEVEL[data_level] is LogLevel.CONFIDENTIAL:
        return LogLevel.CONFIDENTIAL
    return LogLevel.RESTRICTED


SENSITIVE_TYPE_DEFAULT_LEVEL: Mapping[SensitiveType, SensitivityLevel] = MappingProxyType(
    {
        SensitiveType.RESIDENT_REGISTRATION_NUMBER: SensitivityLevel.L3,
        SensitiveType.FOREIGNER_REGISTRATION_NUMBER: SensitivityLevel.L3,
        SensitiveType.CARD_NUMBER: SensitivityLevel.L3,
        SensitiveType.BANK_ACCOUNT: SensitivityLevel.L2,
        SensitiveType.PHONE_NUMBER: SensitivityLevel.L2,
        SensitiveType.EMAIL: SensitivityLevel.L2,
        SensitiveType.IP_ADDRESS: SensitivityLevel.L2,
        SensitiveType.NAME: SensitivityLevel.L2,
        SensitiveType.ADDRESS: SensitivityLevel.L2,
        SensitiveType.DATE_OF_BIRTH: SensitivityLevel.L2,
        SensitiveType.BUSINESS_REGISTRATION_NUMBER: SensitivityLevel.L1,
    }
)
"""민감 종류별 기본 데이터 등급. 탐지기 결과가 우선이고 없을 때 기본값으로 쓴다.

비밀 키·API 키는 컬럼이 아니라 설정에서 오는 값이라 SensitiveType에 없다(로그 L3
고정). 내부 식별자(데이터 L1)를 SensitiveType으로 표현하는 계약 확장은 별도 과업.
"""


class TransmissionRule(FrozenPolicy):
    """외부 LLM 전송 모드 하나의 허용 범위."""

    mode: SecureMode
    sends_samples: bool = Field(description="샘플 행(마스킹 포함)이 외부로 나가는지")
    sends_metadata: bool = Field(description="통계·형태·분포 등 메타데이터가 나가는지")
    sends_raw_values: bool = Field(
        description=(
            "비민감(데이터 L0·L1) 컬럼의 실제 셀 값이 나가는지. "
            "True면 민감(L2·L3) 컬럼은 마스킹한 뒤에만 나간다"
        )
    )
    category_label_allowlist: bool = Field(
        default=False,
        description="사용자 지정 허용 목록에 있는 범주 라벨의 원문 전송 허용 여부",
    )
    note: str


TRANSMISSION_RULES: Mapping[SecureMode, TransmissionRule] = MappingProxyType(
    {
        SecureMode.OFF: TransmissionRule(
            mode=SecureMode.OFF,
            sends_samples=True,
            sends_metadata=True,
            sends_raw_values=True,
            note="비민감 컬럼의 원문 셀 값이 나간다. 민감(L2·L3) 컬럼은 자동 마스킹 후에만. "
            "사용자가 위험을 수용한 모드",
        ),
        SecureMode.SECURE: TransmissionRule(
            mode=SecureMode.SECURE,
            sends_samples=False,
            sends_metadata=True,
            sends_raw_values=False,
            category_label_allowlist=True,
            note="메타데이터만. 컬럼명은 옵션 별칭, 범주는 토큰(허용 목록 라벨은 예외), "
            "min/max는 구간화. 원문 판단은 로컬 모델의 추상 설명으로 대체",
        ),
        SecureMode.STRICT: TransmissionRule(
            mode=SecureMode.STRICT,
            sends_samples=False,
            sends_metadata=False,
            sends_raw_values=False,
            note="외부 전송 0건. 전 과정 로컬·사내 신뢰 구역 모델. "
            "외부 터널(예: unsloth --secure) 경유 엔드포인트는 불허",
        ),
    }
)

FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "셀 값을 f-string·포맷·연결로 로그·예외 메시지에 끼워 넣기",
    "traceback에 지역 변수(행·데이터프레임·프롬프트 원문) 노출",
    "개발용 print·디버그 출력을 커밋",
    "감사 로그·DB 메타데이터에 실제 값 저장",
    "Secure 모드 페이로드에 개별 값이 드러나는 min/max, 허용 목록 밖의 실제 범주 라벨, "
    "자유 텍스트 발췌 포함",
    "예외 원문을 상위 로그로 그대로 전파",
    "외부 라이브러리 텔레메트리·사용 통계 전송 활성화",
    "작업 종료 후 임시 파일·캐시에 원본 데이터 평문 잔존",
    "저엔트로피 값(전화번호·주민등록번호 등)의 무키 해시를 감사 기록에 남기기",
)
"""코드 리뷰·카나리 테스트가 찾는 금지 패턴. 문서 3절 목록과 문장이 같아야 한다."""
