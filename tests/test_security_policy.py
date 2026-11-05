"""데이터·로그 등급 정책 v0 정합성 검증.

문서(docs/policy/data-log-levels.md)의 표와 목록을 파싱해 코드 정의와 필드별로
대조한다. 문구만 겹치는지 보는 게 아니라 규칙이 어긋나면 실패한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoetl.contracts import SensitivityLevel
from autoetl.contracts.sensitivity import SensitiveType
from autoetl.security import (
    CANARY_PREFIX,
    DATA_RAW_VALUE_LOG_LEVEL,
    DEFAULT_SECURE_MODE,
    FORBIDDEN_PATTERNS,
    LOG_LEVEL_POLICY,
    PRIVATE_NETWORKS,
    SENSITIVE_TYPE_DEFAULT_LEVEL,
    TRANSMISSION_RULES,
    LogLevel,
    SecureMode,
    TrustZone,
    allows_metadata,
    allows_raw_value,
    is_registrable_internal,
    log_level_for_value,
    prompt_log_level,
    trust_zone,
)

POLICY_DOC = (
    Path(__file__).resolve().parents[1] / "docs" / "policy" / "data-log-levels.md"
).read_text(encoding="utf-8")


def _doc_lines() -> list[str]:
    return POLICY_DOC.splitlines()


def _table_rows(prefix: str) -> dict[str, list[str]]:
    """'| Off | ... |' 형태의 표 행을 첫 칸을 키로 잘라 돌려준다."""
    rows: dict[str, list[str]] = {}
    for line in _doc_lines():
        if line.startswith(f"| {prefix}"):
            cells = [cell.strip().strip("*") for cell in line.strip("|").split("|")]
            rows[cells[0]] = cells
    return rows


def _allowed(cell: str) -> bool:
    assert cell in {"허용", "금지", "마스킹 후 허용"}, f"알 수 없는 표 값: {cell}"
    return cell != "금지"


# ── 로그 등급 규칙 ────────────────────────────────────────────────────────────


def test_all_log_levels_defined() -> None:
    assert set(LOG_LEVEL_POLICY) == set(LogLevel)
    assert [level.value for level in LogLevel] == ["L0", "L1", "L2", "L3"]


def test_l3_never_recorded() -> None:
    restricted = LOG_LEVEL_POLICY[LogLevel.RESTRICTED]
    assert not restricted.may_record
    assert restricted.audit_alternative


def test_l2_requires_debug_and_encryption() -> None:
    confidential = LOG_LEVEL_POLICY[LogLevel.CONFIDENTIAL]
    assert confidential.may_record
    assert confidential.requires_explicit_debug
    assert confidential.encrypted_at_rest


def test_l0_l1_always_recordable_plain() -> None:
    for level in (LogLevel.OPERATIONAL, LogLevel.INTERNAL):
        policy = LOG_LEVEL_POLICY[level]
        assert policy.may_record
        assert not policy.requires_explicit_debug
        assert not policy.encrypted_at_rest


def test_retention_machine_readable() -> None:
    assert LOG_LEVEL_POLICY[LogLevel.OPERATIONAL].retention_hours == 24 * 30
    assert LOG_LEVEL_POLICY[LogLevel.INTERNAL].retention_hours == 24 * 30
    assert LOG_LEVEL_POLICY[LogLevel.OPERATIONAL].retention_configurable
    assert LOG_LEVEL_POLICY[LogLevel.CONFIDENTIAL].retention_hours == 24
    assert not LOG_LEVEL_POLICY[LogLevel.CONFIDENTIAL].retention_configurable
    assert LOG_LEVEL_POLICY[LogLevel.RESTRICTED].retention_hours is None


def test_policy_tables_read_only() -> None:
    with pytest.raises(TypeError):
        LOG_LEVEL_POLICY[LogLevel.OPERATIONAL] = LOG_LEVEL_POLICY[  # type: ignore[index]
            LogLevel.OPERATIONAL
        ]
    with pytest.raises(TypeError):
        TRANSMISSION_RULES[SecureMode.OFF] = TRANSMISSION_RULES[  # type: ignore[index]
            SecureMode.OFF
        ]
    with pytest.raises(ValidationError):
        TRANSMISSION_RULES[SecureMode.SECURE].sends_samples = True
    assert isinstance(FORBIDDEN_PATTERNS, tuple)


def test_audit_alternatives_forbid_value_hashes() -> None:
    assert "해시는 금지" in LOG_LEVEL_POLICY[LogLevel.CONFIDENTIAL].audit_alternative
    assert "해시는 금지" in LOG_LEVEL_POLICY[LogLevel.RESTRICTED].audit_alternative


# ── 데이터 → 로그 등급 매핑 ──────────────────────────────────────────────────


def test_raw_value_mapping_covers_all_data_levels() -> None:
    assert set(DATA_RAW_VALUE_LOG_LEVEL) == set(SensitivityLevel)


def test_log_level_for_value_rules() -> None:
    assert log_level_for_value(SensitivityLevel.L0, masked=False) is LogLevel.CONFIDENTIAL
    assert log_level_for_value(SensitivityLevel.L1, masked=False) is LogLevel.CONFIDENTIAL
    assert log_level_for_value(SensitivityLevel.L2, masked=False) is LogLevel.RESTRICTED
    assert log_level_for_value(SensitivityLevel.L3, masked=False) is LogLevel.RESTRICTED
    for data_level in SensitivityLevel:
        assert log_level_for_value(data_level, masked=True) is LogLevel.CONFIDENTIAL, data_level


def test_sensitive_type_defaults_cover_all() -> None:
    assert set(SENSITIVE_TYPE_DEFAULT_LEVEL) == set(SensitiveType)
    assert SENSITIVE_TYPE_DEFAULT_LEVEL[SensitiveType.RESIDENT_REGISTRATION_NUMBER] is (
        SensitivityLevel.L3
    )
    assert SENSITIVE_TYPE_DEFAULT_LEVEL[SensitiveType.CARD_NUMBER] is SensitivityLevel.L3
    assert SENSITIVE_TYPE_DEFAULT_LEVEL[SensitiveType.PHONE_NUMBER] is SensitivityLevel.L2
    for level in SENSITIVE_TYPE_DEFAULT_LEVEL.values():
        assert level in {SensitivityLevel.L1, SensitivityLevel.L2, SensitivityLevel.L3}


def test_enum_mixing_guard() -> None:
    # 값 표기가 같아 두 열거형은 ==로 같아진다. 혼동 방지 규칙이 문서에 있는지 확인.
    assert SensitivityLevel.L3.value == LogLevel.RESTRICTED.value
    assert "log_level_for_value" in POLICY_DOC


# ── 전송 규칙 ────────────────────────────────────────────────────────────────


def test_default_secure_mode() -> None:
    assert DEFAULT_SECURE_MODE is SecureMode.SECURE
    assert "기본 모드는 **Secure**" in POLICY_DOC


def test_transmission_rules_cover_all_modes() -> None:
    assert set(TRANSMISSION_RULES) == set(SecureMode)
    off = TRANSMISSION_RULES[SecureMode.OFF]
    assert off.sends_samples and off.sends_metadata and off.sends_raw_values
    secure = TRANSMISSION_RULES[SecureMode.SECURE]
    assert secure.sends_metadata
    assert not secure.sends_samples and not secure.sends_raw_values
    assert secure.category_label_allowlist
    strict = TRANSMISSION_RULES[SecureMode.STRICT]
    assert not strict.sends_metadata
    assert not strict.sends_samples and not strict.sends_raw_values
    assert not off.category_label_allowlist and not strict.category_label_allowlist


# ── 신뢰 구역 판정 ────────────────────────────────────────────────────────────


def test_trust_zone_values() -> None:
    assert [zone.value for zone in TrustZone] == ["loopback", "registered_internal", "external"]


def test_trust_zone_loopback_needs_no_registration() -> None:
    # 루프백 대역 전체(127.0.0.0/8, ::1)와 IPv4 매핑 표기는 등록 없이 이 PC다.
    for host in ("127.0.0.1", "127.200.1.1", "::1", "::ffff:127.0.0.1"):
        assert trust_zone(host) is TrustZone.LOOPBACK, host


def test_trust_zone_unregistered_private_is_external() -> None:
    # 등록하지 않았거나 경고를 확인하지 않은 사내 주소는 외부로 판정된다.
    # registered_internal에는 확인을 마친 주소만 올 수 있다(절차·화면은 P3).
    assert trust_zone("10.1.2.3") is TrustZone.EXTERNAL
    assert trust_zone("10.1.2.3", registered_internal=("10.1.2.3",)) is (
        TrustZone.REGISTERED_INTERNAL
    )
    assert trust_zone("172.16.0.9", registered_internal=("10.1.2.3",)) is TrustZone.EXTERNAL


def test_trust_zone_ignores_public_entries_in_registration() -> None:
    # 등록 목록에 공인 주소가 섞여 있어도 외부로 판정한다(방어적 이중 확인).
    assert trust_zone("8.8.8.8", registered_internal=("8.8.8.8",)) is TrustZone.EXTERNAL
    assert trust_zone("203.0.113.7", registered_internal=("203.0.113.7",)) is (TrustZone.EXTERNAL)


def test_trust_zone_dns_name_is_external_until_resolved() -> None:
    # 이름(DNS) 해석은 P2. IP가 아닌 문자열은 등록했어도 해석 전까지 외부다.
    host = "inference.corp.internal"
    assert trust_zone(host) is TrustZone.EXTERNAL
    assert trust_zone(host, registered_internal=(host,)) is TrustZone.EXTERNAL


def test_trust_zone_registration_matches_by_parsed_address() -> None:
    # IPv6 표기 차이(대문자, 0 생략)와 IPv4 매핑 표기도 같은 주소로 매칭한다.
    assert trust_zone("FD00::1", registered_internal=("fd00::1",)) is (
        TrustZone.REGISTERED_INTERNAL
    )
    assert trust_zone("fd00:0:0:0:0:0:0:1", registered_internal=("fd00::1",)) is (
        TrustZone.REGISTERED_INTERNAL
    )
    assert trust_zone("::ffff:10.1.2.3", registered_internal=("10.1.2.3",)) is (
        TrustZone.REGISTERED_INTERNAL
    )


def test_is_registrable_internal_accepts_private_only() -> None:
    for host in ("10.1.2.3", "172.16.0.9", "172.31.255.1", "192.168.10.11", "fd12:3456::7"):
        assert is_registrable_internal(host), host
    # 공인 주소뿐 아니라 루프백(이 PC라 등록 대상 아님)과 링크 로컬도 거부한다.
    for host in (
        "8.8.8.8",
        "1.1.1.1",
        "2001:db8::1",
        "127.0.0.1",
        "::1",
        "169.254.1.1",
        "inference.corp",
    ):
        assert not is_registrable_internal(host), host


def test_private_networks_are_documented() -> None:
    assert isinstance(PRIVATE_NETWORKS, tuple)
    for network in PRIVATE_NETWORKS:
        assert str(network) in POLICY_DOC, str(network)


# ── 구역별 원문 전송 허용 ─────────────────────────────────────────────────────


def test_l3_raw_value_goes_to_loopback_only() -> None:
    # 데이터 L3 원문은 어떤 모드에서도 루프백만 받는다(등록 사내로도 보내지 않는다).
    for mode in SecureMode:
        assert allows_raw_value(TrustZone.LOOPBACK, SensitivityLevel.L3, mode)
        assert not allows_raw_value(TrustZone.REGISTERED_INTERNAL, SensitivityLevel.L3, mode)
        assert not allows_raw_value(TrustZone.EXTERNAL, SensitivityLevel.L3, mode)


def test_registered_internal_accepts_l0_to_l2() -> None:
    for mode in SecureMode:
        for level in (SensitivityLevel.L0, SensitivityLevel.L1, SensitivityLevel.L2):
            assert allows_raw_value(TrustZone.REGISTERED_INTERNAL, level, mode), (mode, level)


def test_loopback_accepts_everything_in_every_mode() -> None:
    for mode in SecureMode:
        for level in SensitivityLevel:
            assert allows_raw_value(TrustZone.LOOPBACK, level, mode), (mode, level)


def test_external_rules_match_transmission_rules() -> None:
    # 외부에 대한 구역 판정은 기존 모드 표(TRANSMISSION_RULES)와 어긋나지 않는다.
    for mode in SecureMode:
        rule = TRANSMISSION_RULES[mode]
        for level in (SensitivityLevel.L0, SensitivityLevel.L1):
            assert allows_raw_value(TrustZone.EXTERNAL, level, mode) is rule.sends_raw_values
        for level in (SensitivityLevel.L2, SensitivityLevel.L3):
            assert not allows_raw_value(TrustZone.EXTERNAL, level, mode)  # L2는 Off에서도 마스킹 후
        assert allows_metadata(TrustZone.EXTERNAL, mode) is rule.sends_metadata


def test_strict_external_is_zero_transmission() -> None:
    for level in SensitivityLevel:
        assert not allows_raw_value(TrustZone.EXTERNAL, level, SecureMode.STRICT)
    assert not allows_metadata(TrustZone.EXTERNAL, SecureMode.STRICT)
    # Strict은 신뢰 구역(이 PC·등록 사내)은 계속 쓴다.
    for zone in (TrustZone.LOOPBACK, TrustZone.REGISTERED_INTERNAL):
        assert allows_metadata(zone, SecureMode.STRICT)
        assert allows_raw_value(zone, SensitivityLevel.L0, SecureMode.STRICT)


# ── 프롬프트·응답 원문의 로그 등급 ────────────────────────────────────────────


def test_prompt_log_level_follows_highest_contained_value() -> None:
    assert prompt_log_level() is LogLevel.INTERNAL  # 메타데이터만
    assert prompt_log_level(()) is LogLevel.INTERNAL
    assert prompt_log_level((SensitivityLevel.L0,)) is LogLevel.CONFIDENTIAL
    assert prompt_log_level((SensitivityLevel.L0, SensitivityLevel.L1)) is LogLevel.CONFIDENTIAL
    assert prompt_log_level((SensitivityLevel.L0, SensitivityLevel.L2)) is LogLevel.RESTRICTED
    assert prompt_log_level((SensitivityLevel.L1, SensitivityLevel.L3)) is LogLevel.RESTRICTED
    assert prompt_log_level((SensitivityLevel.L3,)) is LogLevel.RESTRICTED


def test_prompt_log_level_masked_values_stay_l2() -> None:
    # 가림 처리한 값만 담긴 프롬프트: 원문이 없으므로 로그 L2에 머문다.
    assert prompt_log_level(masked_value_present=True) is LogLevel.CONFIDENTIAL
    assert prompt_log_level((SensitivityLevel.L0,), masked_value_present=True) is (
        LogLevel.CONFIDENTIAL
    )


def test_prompt_log_level_matches_value_rule() -> None:
    # 담긴 값이 하나뿐일 때는 셀 값 규칙(log_level_for_value)과 같은 등급을 낸다.
    for level in SensitivityLevel:
        assert prompt_log_level((level,)) is log_level_for_value(level, masked=False)


def test_prompt_log_level_secret_values_are_l3() -> None:
    # 비밀값(데이터 컬럼 밖의 API 키·토큰) 표시: 다른 내용과 무관하게 로그 L3.
    assert prompt_log_level(secret_value_present=True) is LogLevel.RESTRICTED
    assert prompt_log_level((SensitivityLevel.L0,), secret_value_present=True) is (
        LogLevel.RESTRICTED
    )
    assert prompt_log_level(masked_value_present=True, secret_value_present=True) is (
        LogLevel.RESTRICTED
    )


def test_prompt_log_level_response_never_below_request() -> None:
    # LLM 응답은 요청 프롬프트의 값을 되풀이할 수 있다: 응답 등급 ≥ 요청 등급.
    assert prompt_log_level(at_least=LogLevel.CONFIDENTIAL) is LogLevel.CONFIDENTIAL
    assert prompt_log_level((SensitivityLevel.L0,), at_least=LogLevel.RESTRICTED) is (
        LogLevel.RESTRICTED
    )
    assert prompt_log_level(at_least=LogLevel.RESTRICTED) is LogLevel.RESTRICTED
    # 하한은 올리기만 한다 — 내용이 더 민감하면 내용 등급이 이긴다.
    assert prompt_log_level((SensitivityLevel.L3,), at_least=LogLevel.CONFIDENTIAL) is (
        LogLevel.RESTRICTED
    )


# ── 문서 ↔ 코드 대조 ──────────────────────────────────────────────────────────


def test_doc_log_table_matches_code() -> None:
    rows = _table_rows("L")
    assert set(rows) == {"L0", "L1", "L2", "L3"}
    expected_retention = {
        LogLevel.OPERATIONAL: ("30일", 24 * 30),
        LogLevel.INTERNAL: ("30일", 24 * 30),
        LogLevel.CONFIDENTIAL: ("24시간", 24),
        LogLevel.RESTRICTED: ("—", None),
    }
    for value, level in (
        ("L0", LogLevel.OPERATIONAL),
        ("L1", LogLevel.INTERNAL),
        ("L2", LogLevel.CONFIDENTIAL),
        ("L3", LogLevel.RESTRICTED),
    ):
        policy = LOG_LEVEL_POLICY[level]
        record_cell = rows[value][3]
        if policy.may_record:
            assert "절대 기록하지 않는다" not in record_cell
        else:
            assert "절대 기록하지 않는다" in record_cell
        if policy.requires_explicit_debug:
            assert "직접 켰을 때만" in record_cell
        retention_text, retention_hours = expected_retention[level]
        assert policy.retention_hours == retention_hours
        assert retention_text in rows[value][4]


def test_doc_transmission_table_matches_code() -> None:
    rows = _table_rows("Off") | _table_rows("Secure") | _table_rows("Strict")
    assert set(rows) == {"Off", "Secure", "Strict"}
    for name, mode in (
        ("Off", SecureMode.OFF),
        ("Secure", SecureMode.SECURE),
        ("Strict", SecureMode.STRICT),
    ):
        rule = TRANSMISSION_RULES[mode]
        cells = rows[name]
        # 칸 순서: 모드 | 샘플 행 | 메타데이터 | 실제 셀 값(비민감) | 비고
        assert _allowed(cells[1]) is rule.sends_samples, (name, "샘플")
        assert _allowed(cells[2]) is rule.sends_metadata, (name, "메타")
        assert _allowed(cells[3]) is rule.sends_raw_values, (name, "셀")


def _zone_cell_modes(cell: str) -> dict[SecureMode, bool]:
    """구역 표 칸('모든 모드 허용', 'Off만 허용', …)을 모드별 허용으로 바꾼다."""
    if cell.startswith("모든 모드 허용"):
        return dict.fromkeys(SecureMode, True)
    if cell.startswith("모든 모드 거부") or cell.startswith("거부"):
        return dict.fromkeys(SecureMode, False)
    if cell == "Off만 허용":
        return {
            SecureMode.OFF: True,
            SecureMode.SECURE: False,
            SecureMode.STRICT: False,
        }
    if cell == "Off·Secure만 허용":
        return {
            SecureMode.OFF: True,
            SecureMode.SECURE: True,
            SecureMode.STRICT: False,
        }
    raise AssertionError(f"알 수 없는 구역 표 값: {cell}")


def test_doc_trust_zone_table_matches_code() -> None:
    rows = _table_rows("루프백") | _table_rows("등록 사내") | _table_rows("외부")
    assert set(rows) == {"루프백(이 PC)", "등록 사내", "외부"}
    zone_by_name = {
        "루프백(이 PC)": TrustZone.LOOPBACK,
        "등록 사내": TrustZone.REGISTERED_INTERNAL,
        "외부": TrustZone.EXTERNAL,
    }
    # 칸 순서: 구역 | 정의 | L0·L1 원문 | L2 원문 | L3 원문 | 메타데이터
    columns = {
        2: (SensitivityLevel.L0, SensitivityLevel.L1),
        3: (SensitivityLevel.L2,),
        4: (SensitivityLevel.L3,),
    }
    for name, zone in zone_by_name.items():
        cells = rows[name]
        assert len(cells) == 6, name
        for column, levels in columns.items():
            allowed_by_mode = _zone_cell_modes(cells[column])
            for mode in SecureMode:
                for level in levels:
                    assert allows_raw_value(zone, level, mode) is allowed_by_mode[mode], (
                        name,
                        cells[column],
                        mode,
                        level,
                    )
        metadata_by_mode = _zone_cell_modes(cells[5])
        for mode in SecureMode:
            assert allows_metadata(zone, mode) is metadata_by_mode[mode], (name, mode)


def test_doc_covers_trust_zone_and_prompt_rules() -> None:
    for token in (
        "trust_zone",
        "allows_raw_value",
        "allows_metadata",
        "prompt_log_level",
        "PRIVATE_NETWORKS",
        "is_registrable_internal",
        "fc00::/7",
        "실패 닫힘",
        "이 PC 또는 등록한 사내 서버의 모델",
        "secret_value_present",
        "비밀값",
        "낮아질 수 없다",
        "이 PC에서 도는 모델",
    ):
        assert token in POLICY_DOC, token


def test_doc_forbidden_patterns_match_code_exactly() -> None:
    section = POLICY_DOC.split("## 3. 금지 패턴", 1)[1].split("## 4.", 1)[0]
    doc_items = re.findall(r"^\d+\. (.+)$", section, flags=re.MULTILINE)
    assert doc_items == list(FORBIDDEN_PATTERNS)


def test_doc_covers_required_topics() -> None:
    for token in (
        "절대 기록하지 않는다",
        "카나리",
        "CANARY-",
        "structlog",
        "허용 목록",
        "신뢰 구역",
        "HMAC",
        "retention_hours",
        "체크섬까지 유효한 합성",
        "top_values",
    ):
        assert token in POLICY_DOC, token


def test_canary_prefix_shape() -> None:
    assert CANARY_PREFIX.startswith("CANARY")
