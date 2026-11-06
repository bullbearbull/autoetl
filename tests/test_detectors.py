"""내장 탐지기 단위 검증(T-0025) — 형태 규칙·함정 게이트·이름 힌트 경계."""

from __future__ import annotations

import pytest

from autoetl.adapters.detectors import name_matches
from autoetl.adapters.detectors.builtin import (
    BUILTIN_DETECTORS,
    _detect_boolean,
    _detect_code,
    _detect_decimal,
    _detect_email,
    _detect_float,
    _detect_identifier,
    _detect_time,
    _detect_url,
)
from autoetl.contracts import (
    ColumnProfile,
    LengthStats,
    PhysicalType,
    SemanticType,
)
from autoetl.core.stages.s02_profile import DEFAULT_MISSING_MARKERS


def _profile(unique: int, count: int) -> ColumnProfile:
    ratio = unique / count if count else 0.0
    return ColumnProfile(
        column="col",
        count=count,
        missing_count=0,
        missing_rate=0.0,
        unique_count=unique,
        cardinality_ratio=ratio,
        length=LengthStats(minimum=0, maximum=0, mean=0.0, p50=0.0, p95=0.0),
    )


def test_default_markers_unchanged() -> None:
    # S3 결측 규칙은 S2 기본 집합을 그대로 쓴다(단계 간 일치).
    expected = frozenset({"-", "N/A", "n/a", "NULL", "null", "해당없음"})
    assert expected == DEFAULT_MISSING_MARKERS


def test_email_detector() -> None:
    from autoetl.adapters.detectors import ColumnSample

    sample = ColumnSample(
        column="email", values=("user1@example.com", "a@b.co.kr"), profile=_profile(2, 2)
    )
    guess = _detect_email(sample)
    assert guess is not None
    assert guess.physical_type is PhysicalType.STRING
    assert guess.semantic_type is SemanticType.EMAIL
    assert guess.confidence == 1.0
    assert _detect_email(ColumnSample("e", ("not-an-email",), _profile(1, 1))) is None


def test_url_and_time_and_boolean() -> None:
    from autoetl.adapters.detectors import ColumnSample

    url = ColumnSample("u", ("https://example.com/a", "http://b.io"), _profile(2, 2))
    url_guess = _detect_url(url)
    assert url_guess is not None and url_guess.semantic_type is SemanticType.URL

    time = ColumnSample("t", ("09:30", "23:59:00", "0:00"), _profile(3, 3))
    guess = _detect_time(time)
    assert guess is not None and guess.physical_type is PhysicalType.TIME

    boolean = ColumnSample("flag", ("Y", "N", "예", "아니오", "O", "x", "TRUE"), _profile(2, 7))
    guess = _detect_boolean(boolean)
    assert guess is not None and guess.physical_type is PhysicalType.BOOLEAN

    # 1/0은 약한 근거라 불리언으로 판정하지 않는다.
    zeros_ones = ColumnSample("flag", ("1", "0", "1"), _profile(2, 3))
    assert _detect_boolean(zeros_ones) is None


def test_time_rejects_out_of_range() -> None:
    from autoetl.adapters.detectors import ColumnSample

    sample = ColumnSample("t", ("24:00", "12:60"), _profile(2, 2))
    assert _detect_time(sample) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2025-06-16", True),
        ("2025/06/16", True),
        ("2025.06.16", True),
        ("20250616", True),
        ("45824", True),  # 엑셀 시리얼(2025-06-16)
        ("19999", False),  # 시리얼 범위 밖(1954-10-01 이전)
        ("60001", False),  # 시리얼 범위 밖(2064-05-04 이후)
        ("2025-13-01", False),  # 없는 달
        ("가나다", False),
    ],
)
def test_date_value_shapes(value: str, expected: bool) -> None:
    from autoetl.adapters.detectors.builtin import _is_date_value

    assert _is_date_value(value) is expected


def test_code_detector_requires_leading_zero() -> None:
    from autoetl.adapters.detectors import ColumnSample

    padded = ColumnSample("code", ("000123", "456789", "099999"), _profile(3, 3))
    guess = _detect_code(padded)
    assert guess is not None
    assert guess.physical_type is PhysicalType.STRING  # 정수로 읽으면 0을 잃는다
    assert guess.semantic_type is SemanticType.CODE
    assert guess.pad_width == 6

    # 앞자리 0이 하나도 없으면 코드 근거가 없다(일반 정수).
    plain = ColumnSample("n", ("123", "45678"), _profile(2, 2))
    assert _detect_code(plain) is None

    # '0' 하나는 0일 뿐 패딩 근거가 아니다.
    zero = ColumnSample("n", ("0", "42"), _profile(2, 2))
    assert _detect_code(zero) is None


def test_decimal_detector_currency_and_precision() -> None:
    from autoetl.adapters.detectors import ColumnSample

    sample = ColumnSample("amount", ("45000", "45,000", "45,000원", "₩45,000"), _profile(4, 4))
    guess = _detect_decimal(sample)
    assert guess is not None
    assert guess.physical_type is PhysicalType.DECIMAL
    assert guess.confidence == 1.0
    assert guess.scale == 0
    assert guess.precision == 5  # 정수부 최대 5자리(45000)

    frac = ColumnSample("v", ("1.50", "22.375"), _profile(2, 2))
    guess = _detect_decimal(frac)
    assert guess is not None
    assert (guess.scale, guess.precision) == (3, 5)

    assert _detect_decimal(ColumnSample("v", ("abc",), _profile(1, 1))) is None


def test_float_detector_exponent() -> None:
    from autoetl.adapters.detectors import ColumnSample

    sample = ColumnSample("v", ("1E5", "3.14e-2"), _profile(2, 2))
    guess = _detect_float(sample)
    assert guess is not None and guess.physical_type is PhysicalType.FLOAT


def test_identifier_requires_unique_integers() -> None:
    from autoetl.adapters.detectors import ColumnSample

    unique = ColumnSample("id", ("1", "2", "3"), _profile(3, 3))
    guess = _detect_identifier(unique)
    assert guess is not None and guess.semantic_type is SemanticType.IDENTIFIER

    repeated = ColumnSample("fk", ("1", "2", "3"), _profile(2, 3))  # 비율 0.67
    assert _detect_identifier(repeated) is None


def test_builtin_detector_registration() -> None:
    # 등록은 코드 목록(진입점 없음). 이름·우선순위가 유일해야 결정론이 성립한다.
    names = [detector.name for detector in BUILTIN_DETECTORS]
    assert len(names) == len(set(names))
    priorities = [detector.priority for detector in BUILTIN_DETECTORS]
    assert len(priorities) == len(set(priorities))


@pytest.mark.parametrize(
    ("column", "keyword", "expected"),
    [
        ("unit_price", "price", True),
        ("list_price", "price", True),
        ("total_amount", "amount", True),
        ("discount_rate", "count", False),  # 'discount' 안의 'count'에 걸리지 않는다
        ("quantity", "quantity", True),
        ("총금액", "금액", True),
        ("메모", "금액", False),
    ],
)
def test_name_hint_word_boundary(column: str, keyword: str, expected: bool) -> None:
    # 밑줄·하이픈을 경계로 본다. 붙여 쓴 camelCase 합성(unitAmount)은 경계가
    # 없어 일치하지 않는다(오인 방지가 우선).
    assert name_matches(column, (keyword,)) is expected
