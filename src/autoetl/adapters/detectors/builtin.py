"""내장 탐지기(T-0025) — 리드 결정 목록 그대로.

정수, 소수(decimal — 천 단위 콤마·'₩'·'원' 허용, 정밀도·자릿수 기록), 실수,
불리언 유사(Y/N·예/아니오·O/X·true/false — 1/0은 약한 근거라 쓰지 않는다),
날짜(yyyy-MM-dd·yyyy/MM/dd·yyyy.MM.dd·yyyyMMdd·엑셀 시리얼, 시리얼은
1954~2064 범위로 제한), 일시, 시간, 이메일, URL, 코드(전부 숫자 + 앞자리 0 —
앞자리 0이 있으면 정수로 읽으면 값을 잃는다), 식별자(유일한 정수),
범주(낮은 카디널리티), 자유 텍스트 폴백.

적합률은 결측 표기를 뺀 표본에서 그 타입 형태와 맞는 값의 비율이다. 우선순위는
적합률 동률일 때만 순서를 정한다(값이 큰 쪽이 이긴다). 특수형(이메일·시간·
불리언·날짜·코드)이 일반형(정수·범주·자유 텍스트)보다 우선한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from autoetl.adapters.detectors import (
    HINT_KEYWORDS,
    ColumnSample,
    Detector,
    NormalizationRule,
    TypeGuess,
    hint_matches,
    name_matches,
)
from autoetl.contracts import PhysicalType, SemanticType

__all__ = ["BUILTIN_DETECTORS", "EXCEL_SERIAL_MAX", "EXCEL_SERIAL_MIN"]

# re.ASCII: \d·\s를 ASCII로만 본다(재검토 4차 정리). 정규화의 DuckDB RE2도
# ASCII만 받아 전각 숫자가 탐지기와 정규화에서 같게 취급된다.
_UNSIGNED_INT = re.compile(r"^\d+$", re.ASCII)
_SIGNED_INT = re.compile(r"^[+-]?\d+$", re.ASCII)
_DECIMAL = re.compile(r"^[₩\s]*[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?[원]?[\s]*$", re.ASCII)
_FLOAT = re.compile(r"^[+-]?\d+(\.\d+)?[eE][+-]?\d+$", re.ASCII)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.ASCII)
_URL = re.compile(r"^https?://\S+$", re.ASCII)
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(:(\d{2}))?$", re.ASCII)
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?$", re.ASCII)

BOOLEAN_TOKENS = frozenset({"y", "n", "예", "아니오", "o", "x", "true", "false"})
"""불리언 유사 표기(소문자 기준). 1/0은 약한 근거라 넣지 않는다."""

DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")
EXCEL_SERIAL_MIN = 20_000  # 1954-10-01: 이보다 작은 정수는 보통 날짜가 아니다.
EXCEL_SERIAL_MAX = 60_000  # 2064-05-04: 이보다 큰 정수는 보통 날짜가 아니다.

CATEGORY_MAX_UNIQUE = 20
"""범주 판정 고유값 상한(또는 카디널리티 비율 0.05 이하)."""

IDENTIFIER_MIN_RATIO = 0.999
"""식별자(유일한 정수) 판정 최소 카디널리티 비율."""


def _fit(values: tuple[str, ...], predicate: Callable[[str], object]) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if predicate(value)) / len(values)


def _is_date_text(value: str) -> bool:
    """문자열 날짜 4형식(ISO·슬래시·점·밀집) 판정."""
    for fmt in DATE_FORMATS:
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


def _is_excel_serial(value: str) -> bool:
    """엑셀 날짜 시리얼(1899-12-30 기준 경과일) 판정 — 범위 제한(1954~2064)."""
    if _SIGNED_INT.fullmatch(value) is None:
        return False
    return EXCEL_SERIAL_MIN <= int(value) <= EXCEL_SERIAL_MAX


def _detect_email(sample: ColumnSample) -> TypeGuess | None:
    fit = _fit(sample.values, lambda v: _EMAIL.fullmatch(v) is not None)
    if fit <= 0.0:
        return None
    return TypeGuess(
        physical_type=PhysicalType.STRING,
        semantic_type=SemanticType.EMAIL,
        confidence=fit,
        normalization=NormalizationRule.IDENTITY,
    )


def _detect_url(sample: ColumnSample) -> TypeGuess | None:
    fit = _fit(sample.values, lambda v: _URL.fullmatch(v) is not None)
    if fit <= 0.0:
        return None
    return TypeGuess(
        physical_type=PhysicalType.STRING,
        semantic_type=SemanticType.URL,
        confidence=fit,
        normalization=NormalizationRule.IDENTITY,
    )


def _detect_boolean(sample: ColumnSample) -> TypeGuess | None:
    fit = _fit(sample.values, lambda v: v.lower() in BOOLEAN_TOKENS)
    if fit <= 0.0:
        return None
    return TypeGuess(
        physical_type=PhysicalType.BOOLEAN,
        semantic_type=SemanticType.BOOLEAN_LIKE,
        confidence=fit,
        normalization=NormalizationRule.BOOLEAN,
    )


def _detect_time(sample: ColumnSample) -> TypeGuess | None:
    def is_time(value: str) -> bool:
        match = _TIME.fullmatch(value)
        if match is None:
            return False
        hour, minute, _, second = match.groups()
        return int(hour) <= 23 and int(minute) <= 59 and (second is None or int(second) <= 59)

    fit = _fit(sample.values, is_time)
    if fit <= 0.0:
        return None
    return TypeGuess(
        physical_type=PhysicalType.TIME,
        semantic_type=SemanticType.DATETIME,
        confidence=fit,
        normalization=NormalizationRule.TIME,
    )


def _detect_timestamp(sample: ColumnSample) -> TypeGuess | None:
    fit = _fit(sample.values, lambda v: _TIMESTAMP.fullmatch(v) is not None)
    if fit <= 0.0:
        return None
    return TypeGuess(
        physical_type=PhysicalType.TIMESTAMP,
        semantic_type=SemanticType.DATETIME,
        confidence=fit,
        normalization=NormalizationRule.TIMESTAMP,
    )


def _detect_date(sample: ColumnSample) -> TypeGuess | None:
    """날짜(문자열 4형식 + 엑셀 시리얼).

    시리얼은 범위 안의 정수라는 것만으로는 날짜 근거가 되지 못한다(재검토
    지적: 이름 힌트 없는 2만~6만 정수 가격이 날짜로 뒤집힌다). 시리얼이
    근거가 되려면 둘 중 하나여야 한다.

    - 같은 컬럼에 문자열 날짜 형식 값이 하나라도 있다(형식 혼재).
    - 컬럼 이름이 날짜형 힌트다(date·dt·일자·날짜·일시).
    """
    text_dates = [value for value in sample.values if _is_date_text(value)]
    serial_fit = _fit(sample.values, _is_excel_serial)
    # 문자열 날짜가 '하나만' 있으면 게이트가 열리지 않는다(재검토 2차: 이름 힌트
    # 없는 2만~6만 정수에 yyyyMMdd 값 하나가 섞이면 가격이 통째로 날짜가 됐다).
    # 코드 패딩 근거와 같은 하한 — 2개 이상 + 표본의 1% 이상.
    text_gate = len(text_dates) >= 2 and len(text_dates) >= 0.01 * len(sample.values)
    serial_is_evidence = text_gate or hint_matches(sample.column, "date")
    fit = (len(text_dates) / len(sample.values) if sample.values else 0.0) + (
        serial_fit if serial_is_evidence else 0.0
    )
    if fit <= 0.0:
        return None
    return TypeGuess(
        physical_type=PhysicalType.DATE,
        semantic_type=SemanticType.DATETIME,
        confidence=fit,
        normalization=NormalizationRule.DATE,
    )


def _detect_code(sample: ColumnSample) -> TypeGuess | None:
    """앞자리 0이 있는 숫자 코드 — 정수로 읽으면 값을 잃는다(0 탈락 함정).

    패딩 근거는 값 하나로 성립하지 않는다(재검토 지적: '07' 하나로 정수
    컬럼 전체가 코드가 됐다). 길이 2 이상이고 0으로 시작하는 값이 2개
    이상이면서 표본의 1% 이상일 때만 코드로 본다. leading_zero 변형처럼
    일부 행에서 0이 떼어져 자릿수가 섞여도 발동한다(자릿수는 최댓값으로
    복원). '0' 하나는 0일 뿐 패딩 근거가 아니다.
    """
    all_digits = _fit(sample.values, lambda v: _UNSIGNED_INT.fullmatch(v) is not None)
    if all_digits <= 0.0:
        return None
    padded = [value for value in sample.values if len(value) > 1 and value.startswith("0")]
    if len(padded) < 2 or len(padded) < 0.01 * len(sample.values):
        return None
    return TypeGuess(
        physical_type=PhysicalType.STRING,
        semantic_type=SemanticType.CODE,
        confidence=all_digits,
        normalization=NormalizationRule.CODE_PAD,
        pad_width=max(len(value) for value in sample.values),
    )


def _decimal_parts(value: str) -> tuple[int, int] | None:
    """콤마·통화 기호를 뺀 (정수부 자릿수, 소수부 자릿수). 아니면 None."""
    if _DECIMAL.fullmatch(value) is None:
        return None
    digits = value.strip().strip("₩").removesuffix("원").replace(",", "").replace(" ", "")
    unsigned = digits.lstrip("+-")
    int_part, _, frac_part = unsigned.partition(".")
    int_digits = len(int_part.lstrip("0")) or 1  # '0.5'의 정수부는 자릿수 1로 센다
    return int_digits, len(frac_part)


def _detect_decimal(sample: ColumnSample) -> TypeGuess | None:
    parts = [_decimal_parts(value) for value in sample.values]
    matched = [part for part in parts if part is not None]
    if not matched:
        return None
    fit = len(matched) / len(sample.values)
    # precision = max(정수부 자릿수) + max(소수부 자릿수), 상한 38.
    int_digits = max(part[0] for part in matched)
    scale = max(part[1] for part in matched)
    semantic = (
        SemanticType.AMOUNT
        if hint_matches(sample.column, "amount")
        else SemanticType.CONTINUOUS_NUMBER
    )
    return TypeGuess(
        physical_type=PhysicalType.DECIMAL,
        semantic_type=semantic,
        confidence=fit,
        normalization=NormalizationRule.DECIMAL,
        precision=min(int_digits + scale, 38),
        scale=scale,
    )


def _detect_float(sample: ColumnSample) -> TypeGuess | None:
    fit = _fit(sample.values, lambda v: _FLOAT.fullmatch(v) is not None)
    if fit <= 0.0:
        return None
    return TypeGuess(
        physical_type=PhysicalType.FLOAT,
        semantic_type=SemanticType.CONTINUOUS_NUMBER,
        confidence=fit,
        normalization=NormalizationRule.FLOAT,
    )


def _detect_identifier(sample: ColumnSample) -> TypeGuess | None:
    fit = _fit(sample.values, lambda v: _SIGNED_INT.fullmatch(v) is not None)
    if fit <= 0.0:
        return None
    if sample.profile.cardinality_ratio < IDENTIFIER_MIN_RATIO:
        return None
    return TypeGuess(
        physical_type=PhysicalType.INTEGER,
        semantic_type=SemanticType.IDENTIFIER,
        confidence=fit,
        normalization=NormalizationRule.INTEGER,
    )


def _detect_integer(sample: ColumnSample) -> TypeGuess | None:
    fit = _fit(sample.values, lambda v: _SIGNED_INT.fullmatch(v) is not None)
    if fit <= 0.0:
        return None
    semantic = (
        SemanticType.COUNT
        if name_matches(sample.column, HINT_KEYWORDS["count"])
        else SemanticType.DISCRETE_NUMBER
    )
    return TypeGuess(
        physical_type=PhysicalType.INTEGER,
        semantic_type=semantic,
        confidence=fit,
        normalization=NormalizationRule.INTEGER,
    )


def _detect_category(sample: ColumnSample) -> TypeGuess | None:
    profile = sample.profile
    if profile.count == 0:
        return None
    if profile.unique_count > CATEGORY_MAX_UNIQUE and profile.cardinality_ratio > 0.05:
        return None
    # 신뢰도는 카디널리티 비율의 역수다 — 값 형태를 보지 않는 탐지기라
    # 1.0을 주장하면 작은 컬럼(이메일 3개 등)을 집어삼킨다.
    confidence = max(0.0, 1.0 - profile.cardinality_ratio)
    if confidence <= 0.0:
        return None
    return TypeGuess(
        physical_type=PhysicalType.STRING,
        semantic_type=SemanticType.NOMINAL_CATEGORY,
        confidence=confidence,
        normalization=NormalizationRule.IDENTITY,
    )


def _detect_free_text(sample: ColumnSample) -> TypeGuess | None:
    """마지막 수단 폴백 — 어떤 값이든 받는다.

    적합률을 1.0보다 조금 낮은 0.97로 둬 임계치(0.98)를 넘지 못하게 한다.
    폴백이 항상 1.0이면 구체적 탐지기와 동률이 돼 모든 컬럼이 '격차 미달'
    로 표시된다. 구체적 후보가 임계치를 못 넘으면 이 폴백이 최선 후보다.
    """
    return TypeGuess(
        physical_type=PhysicalType.STRING,
        semantic_type=SemanticType.FREE_TEXT,
        confidence=0.97,
        normalization=NormalizationRule.IDENTITY,
    )


BUILTIN_DETECTORS: tuple[Detector, ...] = (
    Detector(name="builtin.email", priority=62, detect=_detect_email),
    Detector(name="builtin.url", priority=60, detect=_detect_url),
    Detector(name="builtin.boolean", priority=58, detect=_detect_boolean),
    Detector(name="builtin.time", priority=56, detect=_detect_time),
    Detector(name="builtin.timestamp", priority=54, detect=_detect_timestamp),
    Detector(name="builtin.date", priority=52, detect=_detect_date),
    Detector(name="builtin.code", priority=48, detect=_detect_code),
    Detector(name="builtin.identifier", priority=32, detect=_detect_identifier),
    Detector(
        name="builtin.integer",
        priority=30,
        detect=_detect_integer,
        hint_families=frozenset({"count"}),
    ),
    Detector(name="builtin.float", priority=22, detect=_detect_float),
    Detector(
        name="builtin.decimal",
        priority=20,
        detect=_detect_decimal,
        hint_families=frozenset({"amount"}),
    ),
    Detector(name="builtin.category", priority=26, detect=_detect_category),
    Detector(name="builtin.free_text", priority=0, detect=_detect_free_text),
)
"""등록 목록(v0 코드 목록 등록 — 진입점 없음). 순서는 우선순위 내림차순."""
