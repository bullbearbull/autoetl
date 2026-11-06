"""탐지기 플러그인 인터페이스(T-0025).

탐지기 = 이름 + 우선순위 + 판정 함수 하나. 판정 함수는 컬럼 표본(결측 표기를
뺀 값)과 S2 프로파일을 받아 후보(물리·의미 타입, 적합률, 근거 프로파일 ID,
값 정규화 규칙, decimal 정밀도)를 돌려준다. 맞지 않으면 None.

- 내장 탐지기(`builtin.py`)와 이후 「한국형 도메인 팩」이 같은 인터페이스를
  쓴다. 등록은 v0에서 코드 목록(`builtin.BUILTIN_DETECTORS`)으로 한다.
- 우선순위는 적합률 동률일 때만 순서를 정한다(값이 큰 쪽이 이긴다).
- 적합률(confidence)은 결측 표기를 뺀 표본에서 그 타입 형태와 맞는 값의
  비율이다. 근거 ID는 'ColumnProfile:<컬럼 이름>' 형태로 계약 Evidence와
  대응한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from enum import Enum

from autoetl.contracts import ColumnProfile, PhysicalType, SemanticType

__all__ = [
    "HINT_KEYWORDS",
    "ColumnSample",
    "Detector",
    "NormalizationRule",
    "TypeGuess",
    "hint_matches",
    "name_matches",
]


class NormalizationRule(str, Enum):
    """정규화 뷰가 값에 적용할 규칙(결측 표기 → NULL은 모든 규칙 공통)."""

    IDENTITY = "identity"  # 그대로(문자열)
    INTEGER = "integer"  # 부호 있는 정수로
    DECIMAL = "decimal"  # 천 단위 콤마·₩·'원' 제거 후 decimal로
    FLOAT = "float"  # 지수 표기 포함 실수로
    BOOLEAN = "boolean"  # 불리언 유사 표기를 진짜 불리언으로
    DATE = "date"  # yyyy-MM-dd·/·.·yyyyMMdd·엑셀 시리얼 → ISO 날짜
    TIMESTAMP = "timestamp"  # 일시 표기 → timestamp
    TIME = "time"  # HH:MM(:SS) → time
    CODE_PAD = "code_pad"  # 앞자리 0을 잃은 코드를 고정 자릿수로 복원


@dataclass(frozen=True)
class ColumnSample:
    """탐지기 입력: 컬럼 표본과 S2 프로파일. 표본은 결측 표기를 뺀 값이다."""

    column: str
    values: tuple[str, ...]
    profile: ColumnProfile


@dataclass(frozen=True)
class TypeGuess:
    """탐지기가 낸 후보 하나. S3이 임계치·우선순위로 확정한다."""

    physical_type: PhysicalType
    semantic_type: SemanticType
    confidence: float
    normalization: NormalizationRule
    precision: int | None = None
    scale: int | None = None
    pad_width: int = 0
    """code_pad 정규화 폭(0이면 이 규칙을 쓰지 않는다)."""


@dataclass(frozen=True)
class Detector:
    """탐지기 하나. domain 팩도 같은 모양으로 만들어 목록에 넣는다."""

    name: str
    priority: int
    detect: Callable[[ColumnSample], TypeGuess | None]
    hint_families: frozenset[str] = field(default_factory=frozenset)
    """이 탐지기가 받는 컬럼 이름 힌트 계열(예: {'amount'}). 약한 근거."""


def name_matches(column: str, keywords: Collection[str]) -> bool:
    """컬럼 이름 힌트. 약한 근거로만 쓴다.

    ASCII 키워드는 단어 경계 일치다('discount_rate'의 'count'에 'count' 힌트가
    걸리지 않는다). 밑줄·하이픈은 경계로 취급해 'unit_price'가 'price'에
    걸리게 한다. 한글 키워드는 부분 일치다(붙여 쓰는 합성어 대비).
    """
    text = column.lower().replace("_", "-")
    for keyword in keywords:
        if keyword.isascii():
            if re.search(rf"(?:^|[^a-z0-9]){re.escape(keyword)}(?:[^a-z0-9]|$)", text):
                return True
        elif keyword in text:
            return True
    return False


HINT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "amount": ("price", "amount", "금액", "가격", "단가", "매출", "총액", "cost", "fee"),
    "count": ("count", "수량", "quantity", "qty", "건수", "횟수"),
}
"""힌트 계열별 키워드. S3이 보너스를 줄 때, 탐지기가 의미 타입을 고를 때 쓴다."""


def hint_matches(column: str, family: str) -> bool:
    return name_matches(column, HINT_KEYWORDS.get(family, ()))
