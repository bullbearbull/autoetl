"""보안 등급 정책 v0.

데이터 등급(민감도)은 autoetl.contracts.SensitivityLevel, 로그 등급·전송 모드·
신뢰 구역 규칙은 autoetl.security.levels를 쓴다. 정책 문서는
docs/policy/data-log-levels.md.
"""

from autoetl.security.levels import (
    CANARY_PREFIX,
    DATA_RAW_VALUE_LOG_LEVEL,
    DEFAULT_SECURE_MODE,
    FORBIDDEN_PATTERNS,
    LOG_LEVEL_POLICY,
    PRIVATE_NETWORKS,
    SENSITIVE_TYPE_DEFAULT_LEVEL,
    TRANSMISSION_RULES,
    LogLevel,
    LogPolicy,
    SecureMode,
    TransmissionRule,
    TrustZone,
    allows_metadata,
    allows_raw_value,
    is_loopback_address,
    is_private_address,
    is_registrable_internal,
    log_level_for_value,
    prompt_log_level,
    trust_zone,
)

__all__ = [
    "CANARY_PREFIX",
    "DATA_RAW_VALUE_LOG_LEVEL",
    "DEFAULT_SECURE_MODE",
    "FORBIDDEN_PATTERNS",
    "LOG_LEVEL_POLICY",
    "PRIVATE_NETWORKS",
    "SENSITIVE_TYPE_DEFAULT_LEVEL",
    "TRANSMISSION_RULES",
    "LogLevel",
    "LogPolicy",
    "SecureMode",
    "TransmissionRule",
    "TrustZone",
    "allows_metadata",
    "allows_raw_value",
    "is_loopback_address",
    "is_private_address",
    "is_registrable_internal",
    "log_level_for_value",
    "prompt_log_level",
    "trust_zone",
]
