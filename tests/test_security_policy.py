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
    SENSITIVE_TYPE_DEFAULT_LEVEL,
    TRANSMISSION_RULES,
    LogLevel,
    SecureMode,
    log_level_for_value,
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
