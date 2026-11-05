"""잡음 주입기 — 벤치 난이도 변형을 만든다.

docs/PLAN.md 벤치 절의 잡음 목록(결측, 형식 혼재, CP949 인코딩, 다중 헤더,
앞자리 0)을 구현한다. 모든 주입은 시드 고정 난수로 결정론적이다.
"""

from __future__ import annotations

import random
from datetime import date

NULL_TOKENS = ("-", "N/A", "해당없음")

VARIANTS = ("clean", "missing", "format_mix", "leading_zero", "multi_header", "cp949", "hard")
"""데이터셋 변형 이름. hard는 아래 잡음을 모두 겹친다."""


def _is_date_column(name: str, value: str) -> bool:
    if "_date" not in name:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_amount_column(name: str, value: str) -> bool:
    return ("price" in name or "amount" in name) and value.isdigit()


def _is_code_column(name: str, value: str) -> bool:
    return "code" in name and value.isdigit()


def _missing(rows: list[list[str]], rng: random.Random, rate: float) -> list[list[str]]:
    out = [row[:] for row in rows]
    for row in out:
        for i, value in enumerate(row):
            if value and rng.random() < rate:
                row[i] = rng.choice(NULL_TOKENS)
    return out


def _format_mix(header: list[str], rows: list[list[str]], rng: random.Random) -> list[list[str]]:
    """날짜·금액·불리언 유사값의 형식을 행마다 섞는다."""
    out = [row[:] for row in rows]
    for row in out:
        for i, (name, value) in enumerate(zip(header, row, strict=True)):
            if _is_date_column(name, value):
                parsed = date.fromisoformat(value)
                row[i] = rng.choice(
                    (
                        value,
                        value.replace("-", "/"),
                        value.replace("-", ""),
                        str((parsed - date(1899, 12, 30)).days),  # 엑셀 날짜 시리얼
                    )
                )
            elif _is_amount_column(name, value):
                row[i] = rng.choice(
                    (
                        value,
                        f"{int(value):,}",
                        f"{int(value):,}원",  # cp949는 ₩(U+20A9)을 인코딩하지 못한다
                    )
                )
            elif value in ("예", "아니오"):
                row[i] = rng.choice((value, "Y" if value == "예" else "N"))
    return out


def _leading_zero(header: list[str], rows: list[list[str]], rng: random.Random) -> list[list[str]]:
    """고정 자릿수 코드의 앞자리 0을 일부 행에서 떼어 낸다(파싱 함정)."""
    out = [row[:] for row in rows]
    code_indexes = [i for i, name in enumerate(header) if "code" in name and "products" in name]
    for row in out:
        for i in code_indexes:
            if row[i].startswith("0") and rng.random() < 0.5:
                row[i] = row[i].lstrip("0") or "0"
    return out


def multi_header_rows(header: list[str], group_of: dict[str, str]) -> list[list[str]]:
    """두 행짜리 헤더(1행 = 그룹, 2행 = 필드)를 만든다."""
    top = [group_of.get(name, "") for name in header]
    return [top, list(header)]


def apply_noise(
    variant: str,
    header: list[str],
    rows: list[list[str]],
    *,
    seed: int,
    missing_rate: float = 0.05,
) -> tuple[list[list[str]], str]:
    """변형 이름에 맞게 잡음을 넣는다. 반환: (헤더를 포함한 전체 행, 파일 인코딩)."""
    rng = random.Random(f"noise:{variant}:{seed}")
    body = [row[:] for row in rows]
    encoding = "utf-8"

    if variant in ("missing", "hard"):
        body = _missing(body, rng, missing_rate)
    if variant in ("format_mix", "hard"):
        body = _format_mix(header, body, rng)
    if variant in ("leading_zero", "hard"):
        body = _leading_zero(header, body, rng)

    out_rows = [list(header), *body]
    if variant in ("multi_header", "hard"):
        top = [" | ".join(name.split("_")[:-1]) or name for name in header]
        out_rows = [top, *out_rows]
    if variant in ("cp949", "hard"):
        encoding = "cp949"
    return out_rows, encoding
