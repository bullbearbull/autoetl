"""데이터·로그 등급 정책 v0 정합성 검증."""

from __future__ import annotations

from pathlib import Path

from autoetl.contracts import SensitivityLevel
from autoetl.security import (
    CANARY_PREFIX,
    FORBIDDEN_PATTERNS,
    LOG_LEVEL_POLICY,
    TRANSMISSION_RULES,
    LogLevel,
    SecureMode,
)

POLICY_DOC = Path(__file__).resolve().parents[1] / "docs" / "policy" / "data-log-levels.md"


def test_all_log_levels_defined() -> None:
    assert set(LOG_LEVEL_POLICY) == set(LogLevel)
    assert [level.value for level in LogLevel] == ["L0", "L1", "L2", "L3"]


def test_l3_never_recorded() -> None:
    restricted = LOG_LEVEL_POLICY[LogLevel.RESTRICTED]
    assert not restricted.may_record
    assert restricted.audit_alternative  # 값 대신 남길 것이 있다


def test_l2_requires_debug_and_encryption() -> None:
    confidential = LOG_LEVEL_POLICY[LogLevel.CONFIDENTIAL]
    assert confidential.may_record
    assert confidential.requires_explicit_debug
    assert confidential.encrypted_at_rest
    assert "삭제" in confidential.retention


def test_l0_l1_always_recordable_plain() -> None:
    for level in (LogLevel.OPERATIONAL, LogLevel.INTERNAL):
        policy = LOG_LEVEL_POLICY[level]
        assert policy.may_record
        assert not policy.requires_explicit_debug
        assert not policy.encrypted_at_rest


def test_transmission_rules_cover_all_modes() -> None:
    assert set(TRANSMISSION_RULES) == set(SecureMode)
    off = TRANSMISSION_RULES[SecureMode.OFF]
    # Off는 비민감 원문이 나간다(민감 컬럼은 자동 마스킹). 수용 전제의 모드다.
    assert off.sends_samples and off.sends_metadata and off.sends_raw_values
    secure = TRANSMISSION_RULES[SecureMode.SECURE]
    assert secure.sends_metadata
    assert not secure.sends_samples and not secure.sends_raw_values
    strict = TRANSMISSION_RULES[SecureMode.STRICT]
    assert not strict.sends_metadata
    assert not strict.sends_samples and not strict.sends_raw_values


def test_data_l3_masked_before_l2_debug() -> None:
    # 데이터 L3 원문은 어떤 로그 등급에도 못 들어간다. 마스킹 후에야 L2 디버그 로그 가능.
    confidential = LOG_LEVEL_POLICY[LogLevel.CONFIDENTIAL]
    assert "마스킹" in confidential.contains
    restricted = LOG_LEVEL_POLICY[LogLevel.RESTRICTED]
    assert "데이터 L3" in restricted.contains
    assert "마스킹" in restricted.retention


def test_data_levels_distinct_from_log_levels() -> None:
    # 두 체계는 같은 L0~L3 값을 쓰지만 별개의 열거형이다.
    assert SensitivityLevel.L0.value == LogLevel.OPERATIONAL.value == "L0"
    assert {member.name for member in SensitivityLevel} != {member.name for member in LogLevel}


def test_forbidden_patterns_substantive() -> None:
    assert len(FORBIDDEN_PATTERNS) >= 8
    assert any("f-string" in pattern for pattern in FORBIDDEN_PATTERNS)
    assert any("traceback" in pattern for pattern in FORBIDDEN_PATTERNS)
    assert any("Secure" in pattern for pattern in FORBIDDEN_PATTERNS)


def test_canary_prefix_shape() -> None:
    assert CANARY_PREFIX.startswith("CANARY")


def test_policy_doc_matches_code() -> None:
    doc = POLICY_DOC.read_text(encoding="utf-8")
    for token in (
        "L0",
        "L1",
        "L2",
        "L3",
        "절대 기록하지 않는다",
        "Secure",
        "Strict",
        "카나리",
        "CANARY-",
        "structlog",
    ):
        assert token in doc
    # 금지 패턴 수가 문서와 코드에서 같은 규모를 유지하는지
    assert doc.count("\n8. ") == 1, "금지 패턴은 8개로 문서에 열거된다"
