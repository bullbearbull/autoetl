"""데이터·로그 등급 정책 v0의 기계 판독형 정의.

정책 문서는 docs/policy/data-log-levels.md이고 이 모듈은 그 요점을 코드가 쓸 수
있게 내려놓은 것이다. 규칙 변경은 소유자 검토 대상이다(AGENTS.md 3절). 실제 강제
(로그 가림 처리기, 전송 차단 장치, 카나리 실행)는 P2 이후 이 정의를 따라 만든다.

강제 기준 표이므로 읽기 전용이다. 모델은 frozen이고 표는 MappingProxyType·tuple로
노출한다.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence
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
                "데이터 L0·L1 원문만 담은 프롬프트·응답"
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
                "주민등록번호·카드번호 등), 그 원문을 담은 프롬프트·응답. "
                "비밀 키·API 키"
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


def prompt_log_level(
    raw_value_levels: Sequence[SensitivityLevel] = (),
    *,
    masked_value_present: bool = False,
    secret_value_present: bool = False,
    at_least: LogLevel | None = None,
) -> LogLevel:
    """프롬프트·응답 원문의 로그 등급: 담긴 값 가운데 가장 높은 등급을 따른다.

    문서 1.2·1.3절 충돌 해소 규칙(원문의 등급은 내용물을 따른다).

    - 데이터 L2·L3 컬럼 원문이 하나라도 담겼으면 로그 L3(기록 금지).
    - 데이터 L0·L1 원문만 담겼으면 로그 L2. 가림 처리한 값만 있어도 L2
      (log_level_for_value의 masked 규칙과 같다).
    - 셀 값 원문이 없이 메타데이터만 있으면 로그 L1.
    - 비밀값(API 키·토큰 등 데이터 컬럼 밖의 값)이 담겼다는 표시만으로 로그 L3
      (secret_value_present — 비밀 키는 정책 1.2절에서 로그 L3이다).
    - at_least: 응답 판정에 쓰는 하한. LLM 응답은 받은 프롬프트의 값을 되풀이할
      수 있어 응답의 등급은 요청 프롬프트의 등급보다 낮아질 수 없다. 요청
      프롬프트의 등급을 넘기면 결과는 그 등급 아래로 내려가지 않는다.
    """
    if secret_value_present:
        level = LogLevel.RESTRICTED
    elif raw_value_levels:
        level = max(log_level_for_value(value, masked=False) for value in raw_value_levels)
    elif masked_value_present:
        level = LogLevel.CONFIDENTIAL
    else:
        level = LogLevel.INTERNAL
    if at_least is not None and at_least > level:
        return at_least
    return level


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


class TrustZone(str, Enum):
    """엔드포인트의 신뢰 구역(문서 2.2절)."""

    LOOPBACK = "loopback"  # 이 PC(루프백 대역)
    REGISTERED_INTERNAL = "registered_internal"  # 사용자가 등록·확인한 사내 서버
    EXTERNAL = "external"  # 그 밖의 전부(공인 주소, 등록 안 한 사설 주소, DNS 이름, 터널)


PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)
"""사내 서버 등록이 가능한 사설 대역. 루프백(127.0.0.0/8)과 링크 로컬은 넣지 않는다.

루프백은 '이 PC'라 등록 없이 신뢰 구역이고, 링크 로컬(169.254.0.0/16)은 사내
엔드포인트로 쓰는 경우가 드물어 닫아 둔다(실수로 열리는 쪽보다 안전하다).
"""


def _parsed_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """주소 문자열을 파싱한다. IPv4 매핑 IPv6(::ffff:x.x.x.x)은 IPv4로 정규화한다.

    DNS 이름처럼 IP 주소가 아닌 문자열은 None(해석은 P2에서 연결). 이 판정은
    실패 닫힘이다 — 파싱이 안 되면 외부로 판정된다.
    """
    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_loopback_address(host: str) -> bool:
    """이 PC(루프백 대역 127.0.0.0/8, ::1)인지."""
    address = _parsed_ip(host)
    return address is not None and address.is_loopback


def is_private_address(host: str) -> bool:
    """사설 IP 대역(RFC1918·IPv6 ULA)인지. 루프백·링크 로컬은 포함하지 않는다."""
    address = _parsed_ip(host)
    return address is not None and any(address in network for network in PRIVATE_NETWORKS)


def is_registrable_internal(host: str) -> bool:
    """사내 서버로 등록할 수 있는 주소인지: 사설 대역만 허용, 공인 주소는 거부한다."""
    return is_private_address(host)


def trust_zone(host: str, *, registered_internal: Sequence[str] = ()) -> TrustZone:
    """엔드포인트의 신뢰 구역을 판정한다.

    - 루프백: 등록 없이 신뢰 구역(이 PC).
    - 등록 목록에 있고 사설 주소인 경우: 등록 사내. 등록 목록에는 사용자가 설정에서
      직접 등록하고 경고를 확인한 주소만 올 수 있다(확인 절차·화면은 P3).
    - 그 밖의 전부(등록하지 않은 사설 주소, 공인 주소, DNS 이름, 터널): 외부.
      등록 목록에 공인 주소가 섞여 있어도 여기서는 무시하고 외부로 판정한다(방어).
    """
    address = _parsed_ip(host)
    if address is None:
        return TrustZone.EXTERNAL
    if address.is_loopback:
        return TrustZone.LOOPBACK
    if any(_parsed_ip(entry) == address for entry in registered_internal) and (
        is_private_address(host)
    ):
        return TrustZone.REGISTERED_INTERNAL
    return TrustZone.EXTERNAL


def allows_raw_value(zone: TrustZone, data_level: SensitivityLevel, mode: SecureMode) -> bool:
    """셀 원문 값을 zone 엔드포인트로 보낼 수 있는지(문서 2.2 구역별 허용 표).

    - 루프백(이 PC): 모든 데이터 등급·모든 모드에서 허용. L3 원문을 받는 유일한 구역.
    - 등록 사내: 데이터 L0~L2 원문 허용, L3 원문은 어떤 모드에서도 거부.
    - 외부: Off 모드에서 비민감(L0·L1) 원문만 허용. L2는 Off에서도 마스킹 후.
    """
    if zone is TrustZone.LOOPBACK:
        return True
    if zone is TrustZone.REGISTERED_INTERNAL:
        return data_level is not SensitivityLevel.L3
    return mode is SecureMode.OFF and data_level in (
        SensitivityLevel.L0,
        SensitivityLevel.L1,
    )


def allows_metadata(zone: TrustZone, mode: SecureMode) -> bool:
    """메타데이터 전송 허용 여부. Strict의 외부 전송 0건 규칙을 반영한다.

    신뢰 구역(루프백·등록 사내)은 모든 모드에서 메타데이터를 받을 수 있다.
    """
    if zone is not TrustZone.EXTERNAL:
        return True
    return mode is not SecureMode.STRICT


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
            note="외부에는 메타데이터만. 컬럼명은 옵션 별칭, 범주는 토큰(허용 목록 라벨은 "
            "예외), min/max는 구간화. 원문 판단은 로컬 모델의 추상 설명으로 대체. "
            "신뢰 구역(루프백·등록 사내) 모델에는 원문을 보낼 수 있다(2.2절 구역 표)",
        ),
        SecureMode.STRICT: TransmissionRule(
            mode=SecureMode.STRICT,
            sends_samples=False,
            sends_metadata=False,
            sends_raw_values=False,
            note="외부 전송 0건. 이 PC와 등록한 사내 서버만 쓴다(PLAN의 '전 과정 로컬 "
            "모델'을 '이 PC 또는 등록한 사내 서버의 모델'로 해석). 외부 터널(예: "
            "unsloth --secure) 경유 엔드포인트는 어떤 모드에서도 신뢰 구역이 아니다",
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
