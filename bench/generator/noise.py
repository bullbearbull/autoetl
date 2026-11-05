"""잡음 주입기 — 벤치 난이도 변형을 만든다.

docs/PLAN.md 벤치 절의 잡음 목록(결측, 형식 혼재, CP949 인코딩, 다중 헤더,
앞자리 0)을 구현한다. 모든 주입은 시드 고정 난수로 결정론적이다.
잡음 대상은 컬럼 이름이 아니라 팩 선언의 kind·키 여부로 고른다(헤더에 테이블
접두사가 없어져 이름 기반 판별은 쓰지 않는다).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date

from bench.generator.packs import FlatColumn

NULL_TOKENS = ("-", "N/A", "해당없음")

VARIANTS = ("clean", "missing", "format_mix", "leading_zero", "multi_header", "cp949", "hard")
"""데이터셋 변형 이름. hard는 아래 잡음을 모두 겹친다."""

MULTI_HEADER_VARIANTS = ("multi_header", "hard")
"""두 행짜리 헤더(1행 = 그룹, 2행 = 필드)를 쓰는 변형. hard는 필드 이름도 없다."""

GROUP_LABELS: dict[str, str] = {
    "id": "수치",
    "fk": "수치",
    "int": "수치",
    "date": "날짜",
    "amount": "금액",
    "code": "코드",
    "name_kr": "이름",
    "email": "연락처",
    "phone": "연락처",
    "category": "분류",
    "text": "텍스트",
}
"""다중 헤더 그룹 행 라벨. 컬럼 성격으로 묶되 정답 테이블 배정을 드러내지
않는다. id·fk는 int와 함께 '수치'로 묶는다 — 식별자를 따로 표시하면 평면
컬럼이 테이블별로 이어져 있어 '식별자' 위치가 곧 테이블 경계가 되어,
데이터를 읽지 않고 배정·PK를 복원할 수 있기 때문이다(소유자 재리뷰 지적)."""


def _is_date(kind: str, value: str) -> bool:
    if kind != "date":
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_amount(kind: str, value: str) -> bool:
    return kind == "amount" and value.isdigit()


def _missing(
    rows: list[list[str]], rng: random.Random, rate: float, keys: Sequence[bool]
) -> list[list[str]]:
    """결측 토큰 주입. PK·조인 키 컬럼은 빠진다(공유 answer.json과 어긋나지 않게)."""
    out = [row[:] for row in rows]
    for row in out:
        for i, value in enumerate(row):
            if not keys[i] and value and rng.random() < rate:
                row[i] = rng.choice(NULL_TOKENS)
    return out


def _format_mix(rows: list[list[str]], rng: random.Random, kinds: Sequence[str]) -> list[list[str]]:
    """날짜·금액 값의 형식을 행마다 섞는다."""
    out = [row[:] for row in rows]
    for row in out:
        for i, (kind, value) in enumerate(zip(kinds, row, strict=True)):
            if _is_date(kind, value):
                parsed = date.fromisoformat(value)
                row[i] = rng.choice(
                    (
                        value,
                        value.replace("-", "/"),
                        value.replace("-", ""),
                        str((parsed - date(1899, 12, 30)).days),  # 엑셀 날짜 시리얼
                    )
                )
            elif _is_amount(kind, value):
                row[i] = rng.choice(
                    (
                        value,
                        f"{int(value):,}",
                        f"{int(value):,}원",  # cp949는 ₩(U+20A9)을 인코딩하지 못한다
                    )
                )
    return out


def _leading_zero(
    rows: list[list[str]], rng: random.Random, kinds: Sequence[str]
) -> list[list[str]]:
    """고정 자릿수 코드의 앞자리 0을 일부 행에서 떼어 낸다(파싱 함정)."""
    out = [row[:] for row in rows]
    code_indexes = [i for i, kind in enumerate(kinds) if kind == "code"]
    for row in out:
        for i in code_indexes:
            if row[i].startswith("0") and rng.random() < 0.5:
                row[i] = row[i].lstrip("0") or "0"
    return out


def apply_noise(
    variant: str,
    header: list[str],
    rows: list[list[str]],
    *,
    seed: int,
    columns: Sequence[FlatColumn],
    missing_rate: float = 0.05,
) -> tuple[list[list[str]], str]:
    """변형 이름에 맞게 잡음을 넣는다. 반환: (헤더를 포함한 전체 행, 파일 인코딩).

    hard는 잡음 전부에 이름 없는 헤더(col_01 형식)까지 겹친다. 헤더 행 수는
    변형이 달라도 컬럼 수와 순서는 같다.
    """
    rng = random.Random(f"noise:{variant}:{seed}")
    kinds = [column.kind for column in columns]
    keys = [column.is_key for column in columns]
    body = [row[:] for row in rows]
    encoding = "utf-8"

    if variant in ("missing", "hard"):
        body = _missing(body, rng, missing_rate, keys)
    if variant in ("format_mix", "hard"):
        body = _format_mix(body, rng, kinds)
    if variant in ("leading_zero", "hard"):
        body = _leading_zero(body, rng, kinds)

    field_row = list(header)
    if variant in MULTI_HEADER_VARIANTS:
        top = [GROUP_LABELS[kind] for kind in kinds]
        if variant == "hard":
            field_row = [f"col_{i:02d}" for i in range(1, len(header) + 1)]
        out_rows = [top, field_row, *body]
    else:
        out_rows = [field_row, *body]
    if variant in ("cp949", "hard"):
        encoding = "cp949"
    return out_rows, encoding
