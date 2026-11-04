"""보안 등급 정책 v0.

데이터 등급(민감도)은 autoetl.contracts.SensitivityLevel, 로그 등급과 전송 모드
규칙은 autoetl.security.levels를 쓴다. 정책 문서는 docs/policy/data-log-levels.md.
"""

from autoetl.security.levels import (
    CANARY_PREFIX,
    FORBIDDEN_PATTERNS,
    LOG_LEVEL_POLICY,
    TRANSMISSION_RULES,
    LogLevel,
    LogPolicy,
    SecureMode,
    TransmissionRule,
)

__all__ = [
    "CANARY_PREFIX",
    "FORBIDDEN_PATTERNS",
    "LOG_LEVEL_POLICY",
    "TRANSMISSION_RULES",
    "LogLevel",
    "LogPolicy",
    "SecureMode",
    "TransmissionRule",
]
