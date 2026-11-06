"""S2 구조 프로파일 — ColumnProfile 목록(T-0024).

컬럼별 결측·고유값·길이 분포·형태 마스크를 계산한다.

- 결측 표기 기본 집합: 빈 문자열, 공백만, '-', 'N/A', 'n/a', 'NULL', 'null',
  '해당없음'(인자로 바꿀 수 있다). NULL(따옴표 없는 빈 칸)도 결측이다.
  마커는 값 전체와 정확히 같아야 결측이다(부분 일치 아님).
- 결측 수·고유값 수는 **전수 정확값**이 기본이다(DuckDB 집계). 전수 집계가
  메모리 한도를 넘어 실패하면 근사값(approx_count_distinct)으로 대신하고
  notes에 근사임을 적는다.
- S1이 표본 전략이면 길이 통계·형태 마스크는 표본 테이블에서 뽑고 notes에
  표본 기반임을 적는다(결측 수·고유값 수는 여전히 전수).
- 형태 마스크: 숫자 → '9', 영문 → 'A'/'a'(대소문자 구분), 한글 → '가',
  그 밖의 글자 → 'X', 구두점·기호·공백은 그대로 둔다. 상위 5개와 비율을
  남긴다(비율 합은 1을 넘지 않는다).
- 셀 값은 notes에 넣지 않는다(정책 3절). 컬럼 이름·집계값만 나간다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Collection, Iterable
from typing import Protocol

import duckdb

from autoetl.contracts import (
    ColumnProfile,
    IngestManifest,
    LengthStats,
    PatternMask,
    SamplingPlan,
)
from autoetl.core.context import RAW_TABLE, AnalysisContext, quote_identifier
from autoetl.core.stages.s01_sampling import SAMPLE_RELATION_KEY

DEFAULT_MISSING_MARKERS = frozenset({"-", "N/A", "n/a", "NULL", "null", "해당없음"})
"""결측 표기 기본 집합(빈 문자열·공백만은 조건 자체가 처리한다)."""

PATTERN_TOP_K = 5
"""형태 마스크 상위 개수."""

APPROX_NOTE = "고유값 수는 메모리 한도 초과로 근사값(approx_count_distinct)이다"
SAMPLE_NOTE = "길이·형태 통계는 표본 기반이다(결측 수·고유값 수는 전수)"

PATTERN_FETCH_ROWS = 10_000
"""형태 마스크 집계에서 한 번에 가져오는 (값, 개수) 묶음 크기."""


def pattern_mask(value: str) -> str:
    """값 하나를 형태 마스크로 바꾼다(원문 글자는 남지 않는다)."""

    def mask_char(ch: str) -> str:
        if ch.isascii() and ch.isdigit():
            return "9"
        if ch.isascii() and ch.isalpha():
            return "A" if ch.isupper() else "a"
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF:  # 한글 음절·자모
            return "가"
        if ch.isspace():
            return ch
        if unicodedata.category(ch)[0] in {"P", "S"}:  # 구두점·기호는 그대로
            return ch
        return "X"

    return "".join(mask_char(ch) for ch in value)


class _Executor(Protocol):
    """결측·고유값 집계에 쓰는 최소 연결 인터페이스(근사 폴백 단위 테스트용)."""

    def execute(self, query: str, parameters: Iterable[object] | None = None) -> _Executor: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def fetchmany(self, size: int = ...) -> list[tuple[object, ...]]: ...


def _as_int(value: object) -> int:
    """집계 결과를 int로 좁힌다. COUNT·길이 집계는 정수로 온다."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _as_float(value: object) -> float:
    """집계 결과를 float으로 좁힌다. 평균·분위수는 실수로 온다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _exact_or_approx_unique(
    run_exact: Callable[[], int], run_approx: Callable[[], int]
) -> tuple[int, bool]:
    """전수 정확값을 먼저 시도하고, 메모리 한도 초과면 근사값으로 대신한다."""
    try:
        return run_exact(), False
    except duckdb.OutOfMemoryException:
        return run_approx(), True


def _missing_predicate(column_id: str, markers: Collection[str]) -> tuple[str, list[str]]:
    """결측 판정 SQL 조각과 매개변수를 만든다. 마커는 값 전체 일치다."""
    quoted = quote_identifier(column_id)
    clauses = [f"{quoted} IS NULL", f"regexp_matches({quoted}, '^\\s*$')"]
    parameters: list[str] = []
    for marker in sorted(markers):
        clauses.append(f"{quoted} = ?")
        parameters.append(marker)
    return " OR ".join(f"({clause})" for clause in clauses), parameters


def _unique_count(
    executor: _Executor, table: str, column_id: str, predicate: str, parameters: list[str]
) -> tuple[int, bool]:
    quoted = quote_identifier(column_id)

    def run_exact() -> int:
        row = executor.execute(
            f"SELECT COUNT(DISTINCT {quoted}) FROM {table} WHERE NOT ({predicate})",
            parameters,
        ).fetchone()
        return _as_int(row[0]) if row is not None else 0

    def run_approx() -> int:
        row = executor.execute(
            f"SELECT approx_count_distinct({quoted}) FROM {table} WHERE NOT ({predicate})",
            parameters,
        ).fetchone()
        return _as_int(row[0]) if row is not None else 0

    return _exact_or_approx_unique(run_exact, run_approx)


def _length_stats(
    executor: _Executor, table: str, column_id: str, predicate: str, parameters: list[str]
) -> LengthStats:
    quoted = quote_identifier(column_id)
    row = executor.execute(
        f"SELECT min(length({quoted})), max(length({quoted})), avg(length({quoted})), "
        f"quantile_cont(length({quoted}), 0.5), quantile_cont(length({quoted}), 0.95) "
        f"FROM {table} WHERE NOT ({predicate})",
        parameters,
    ).fetchone()
    values = row if row is not None else (None, None, None, None, None)
    # 값이 하나도 없으면 통계는 0으로 둔다(계약 하한 ge=0).
    return LengthStats(
        minimum=_as_int(values[0]),
        maximum=_as_int(values[1]),
        mean=_as_float(values[2]),
        p50=_as_float(values[3]),
        p95=_as_float(values[4]),
    )


def _top_patterns(
    executor: _Executor,
    table: str,
    column_id: str,
    predicate: str,
    parameters: list[str],
    top_k: int,
) -> list[PatternMask]:
    quoted = quote_identifier(column_id)
    result = executor.execute(
        f"SELECT {quoted}, COUNT(*) FROM {table} WHERE NOT ({predicate}) GROUP BY {quoted}",
        parameters,
    )
    counts: dict[str, int] = {}
    total = 0
    while rows := result.fetchmany(PATTERN_FETCH_ROWS):
        for value, count in rows:
            if not isinstance(value, str) or not isinstance(count, int):
                continue
            mask = pattern_mask(value)
            counts[mask] = counts.get(mask, 0) + count
            total += count
    if not counts:
        return []
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top_k]
    return [PatternMask(mask=mask, count=count, rate=count / total) for mask, count in ordered]


def run(
    ctx: AnalysisContext,
    ingest: IngestManifest,
    sampling: SamplingPlan,
    *,
    missing_markers: Collection[str] = DEFAULT_MISSING_MARKERS,
    pattern_top_k: int = PATTERN_TOP_K,
) -> list[ColumnProfile]:
    """컬럼별 구조 프로파일. 전수 집계는 raw, 표본 통계는 S1 표본 테이블에서."""
    sampled = sampling.strategy == "sample"
    sample_relation = ctx.stage_artifacts.get(SAMPLE_RELATION_KEY)
    profile_table = sample_relation if sampled and isinstance(sample_relation, str) else RAW_TABLE
    total_rows = ctx.row_count

    profiles: list[ColumnProfile] = []
    for column in ingest.columns:
        column_id = ctx.column_id_of(column.index)
        predicate, base_parameters = _missing_predicate(column_id, missing_markers)

        row = ctx.connection.execute(
            f"SELECT COUNT(*) FROM {RAW_TABLE} WHERE {predicate}", base_parameters
        ).fetchone()
        missing_count = _as_int(row[0]) if row is not None else 0
        count = total_rows - missing_count
        unique_count, approximated = _unique_count(
            ctx.connection, RAW_TABLE, column_id, predicate, base_parameters
        )

        notes: list[str] = []
        if approximated:
            notes.append(APPROX_NOTE)
        if sampled:
            notes.append(SAMPLE_NOTE)

        profiles.append(
            ColumnProfile(
                column=column.name,
                count=count,
                missing_count=missing_count,
                missing_rate=missing_count / total_rows if total_rows else 0.0,
                unique_count=unique_count,
                cardinality_ratio=unique_count / count if count else 0.0,
                length=_length_stats(
                    ctx.connection, profile_table, column_id, predicate, base_parameters
                ),
                top_patterns=_top_patterns(
                    ctx.connection,
                    profile_table,
                    column_id,
                    predicate,
                    base_parameters,
                    pattern_top_k,
                ),
                notes=notes,
            )
        )
    return profiles
