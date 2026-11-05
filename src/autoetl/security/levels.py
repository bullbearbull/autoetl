"""데이터·로그 등급 정책 v0의 기계 판독형 정의.

정책 문서는 docs/policy/data-log-levels.md이고 이 모듈은 그 요점을 코드가 쓸 수
있게 내려놓은 것이다. 규칙 변경은 소유자 검토 대상이다(AGENTS.md 3절). 실제 강제
(로그 가림 처리기, 전송 차단 장치, 카나리 실행)는 P2 이후 이 정의를 따라 만든다.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from autoetl.contracts.common import ContractModel

CANARY_PREFIX = "CANARY-"
"""카나리 테스트에서 심는 표식 접두사. 값은 실행마다 랜덤(UUID)으로 만든다."""


class SecureMode(str, Enum):
    """외부 LLM 전송 모드(docs/PLAN.md Secure Mode 절)."""

    OFF = "off"
    SECURE = "secure"
    STRICT = "strict"


class LogLevel(str, Enum):
    """로그 등급. 높아질수록 기록이 제한된다."""

    OPERATIONAL = "L0"  # 운영
    INTERNAL = "L1"  # 내부
    CONFIDENTIAL = "L2"  # 기밀
    RESTRICTED = "L3"  # 제한


class LogPolicy(ContractModel):
    """로그 등급 하나의 기록·보존 규칙."""

    level: LogLevel
    name: str = Field(description="예: '운영'")
    contains: str = Field(description="이 등급 정보에 담길 수 있는 것")
    may_record: bool = Field(description="로그·저장 매체에 기록 가능한지")
    requires_explicit_debug: bool = Field(
        default=False, description="기록하려면 사용자가 디버그를 직접 켜야 하는지"
    )
    encrypted_at_rest: bool = Field(default=False, description="보존 시 암호화 필수")
    retention: str = Field(description="보존 규칙 요약")
    audit_alternative: str = Field(description="기록 대신 감사에 남길 수 있는 것", default="")


LOG_LEVEL_POLICY: dict[LogLevel, LogPolicy] = {
    LogLevel.OPERATIONAL: LogPolicy(
        level=LogLevel.OPERATIONAL,
        name="운영",
        contains="작업 상태, 단계 진행, 오류 코드, 성능 지표. 데이터 값 없음",
        may_record=True,
        retention="기본 보존(30일), 평문 허용",
        audit_alternative="그대로 기록",
    ),
    LogLevel.INTERNAL: LogPolicy(
        level=LogLevel.INTERNAL,
        name="내부",
        contains="컬럼명, 통계, 형태 마스크 분포, 품질 이슈 유형. 개별 셀 값 없음",
        may_record=True,
        retention="기본 보존(30일)",
        audit_alternative="그대로 기록",
    ),
    LogLevel.CONFIDENTIAL: LogPolicy(
        level=LogLevel.CONFIDENTIAL,
        name="기밀",
        contains=(
            "샘플 값, 데이터가 포함된 프롬프트·응답. "
            "데이터 L2 값(전화·이메일·이름 등)은 이 등급의 암호화 로그에만 담을 수 있다. "
            "데이터 L3 값(주민등록번호·카드번호·키)은 마스킹한 뒤에만 담는다"
        ),
        may_record=True,
        requires_explicit_debug=True,
        encrypted_at_rest=True,
        retention="디버그 직접 활성화 시에만 암호화 기록, 24시간 후 자동 삭제",
        audit_alternative="이벤트 유형과 크기·해시만",
    ),
    LogLevel.RESTRICTED: LogPolicy(
        level=LogLevel.RESTRICTED,
        name="제한",
        contains="데이터 L3 원문: 주민등록번호, 카드번호, 비밀 키, API 키",
        may_record=False,
        retention="기록 자체가 금지. 마스킹·해시·존재 사실은 L2 이하로 기록 가능",
        audit_alternative="존재 사실과 종류·해시만(값 없음)",
    ),
}


class TransmissionRule(ContractModel):
    """외부 LLM 전송 모드 하나의 허용 범위."""

    mode: SecureMode
    sends_samples: bool = Field(description="샘플 행(마스킹 포함)이 외부로 나가는지")
    sends_metadata: bool = Field(description="통계·형태·분포 등 메타데이터가 나가는지")
    sends_raw_values: bool = Field(
        description=(
            "비민감 컬럼의 실제 셀 값이 나가는지. True면 민감(L2·L3) 컬럼은 마스킹한 뒤에만 나간다"
        )
    )
    note: str


TRANSMISSION_RULES: dict[SecureMode, TransmissionRule] = {
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
        note="메타데이터만. 컬럼명은 옵션 별칭, 범주는 토큰, min/max는 구간화."
        " 원문 판단은 로컬 모델의 추상 설명으로 대체",
    ),
    SecureMode.STRICT: TransmissionRule(
        mode=SecureMode.STRICT,
        sends_samples=False,
        sends_metadata=False,
        sends_raw_values=False,
        note="외부 전송 없음. 전 과정 로컬 모델",
    ),
}


FORBIDDEN_PATTERNS: list[str] = [
    "셀 값을 f-string·포맷·연결로 로그·예외 메시지에 끼워 넣기",
    "traceback에 지역 변수(행·데이터프레임·프롬프트 원문) 노출 — locals 수집 금지",
    "개발용 print·주석 처리 안 된 디버그 출력을 커밋",
    "감사 로그·DB 메타데이터에 실제 값 저장 — 해시·집계값만",
    "Secure 모드 페이로드에 개별 값이 드러나는 min/max, 실제 범주 라벨, 자유 텍스트 발췌 포함",
    "예외 원문(e.strerror 등)을 상위 로그로 그대로 전파",
    "외부 라이브러리 텔레메트리·사용 통계 전송 활성화",
    "작업 종료 후 임시 파일·캐시에 원본 데이터 평문 잔존",
]
"""코드 리뷰·카나리 테스트가 찾는 금지 패턴."""
