"""S3 타입 추론 — TypeAssignment 목록과 값 정규화 뷰(T-0025).

- 탐지기(adapters/detectors)가 컬럼 표본·프로파일로 후보를 낸다. 결측 표기를
  뺀 값의 적합률이 임계치(기본 0.98)를 넘은 후보 가운데 (적합률 + 이름 힌트
  보너스, 우선순위)로 확정한다. 컬럼 이름 힌트는 동률을 판결하는 약한 근거다
  — 값 근거(격차)를 뒤집지 못한다.
- 상위 두 후보의 격차가 임계치보다 작으면 'LLM 미확정'으로 표시한다(notes +
  alternatives, llm_resolved=False 유지). P1은 --no-llm이라 최상위 후보로
  확정하고, LLM 질의는 P2가 맡는다.
- 확정 타입으로 값을 정규화한 뷰를 컨텍스트에 둔다(ctx.normalized_view):
  결측 표기 → NULL, 날짜는 ISO(네 형식 + 엑셀 시리얼), 금액은 천 단위
  콤마·'₩'·'원' 제거 후 decimal, 앞자리 0을 잃은 코드는 고정 자릿수로 복원.
  S4·S5·S6·S10이 이 뷰를 쓴다.
- 정규화에 실패한(값이 있는데 NULL이 나온) 값 수를 컬럼별로 세어
  stage_artifacts에 남긴다(S6 유효성 근거). 집계 숫자만 notes에 나간다.
- SQL에는 내부 식별자(c0…)와 리터럴만 들어간다(결측 마커는 설정 상수).
"""

from __future__ import annotations

from autoetl.adapters.detectors import (
    ColumnSample,
    Detector,
    NormalizationRule,
    TypeGuess,
    hint_matches,
)
from autoetl.adapters.detectors.builtin import (
    BUILTIN_DETECTORS,
    EXCEL_SERIAL_MAX,
    EXCEL_SERIAL_MIN,
)
from autoetl.contracts import (
    ColumnProfile,
    IngestManifest,
    PhysicalType,
    SemanticType,
    TypeAlternative,
    TypeAssignment,
)
from autoetl.core.context import RAW_TABLE, AnalysisContext, quote_identifier
from autoetl.core.stages.s01_sampling import DEFAULT_SEED, SAMPLE_RELATION_KEY
from autoetl.core.stages.s02_profile import DEFAULT_MISSING_MARKERS

FIT_THRESHOLD = 0.98
"""후보 채택 최소 적합률(결측 표기를 뺀 값 기준)."""

HINT_BONUS = 0.02
"""이름 힌트 보너스. 값 근거의 격차를 뒤집지 못하는 크기다."""

AMBIGUOUS_GAP = 0.05
"""상위 두 후보 격차가 이보다 작으면 'LLM 미확정'으로 표시한다."""

DETECT_SAMPLE_LIMIT = 1000
"""판정에 쓸 컬럼 값 표본 상한(스캔 순서)."""

NORMALIZED_VIEW = "normalized"
"""값 정규화 뷰 이름. 내부 식별자 규칙([a-z][a-z0-9_]*)을 지킨다."""

FAILURES_KEY = "s03_types.normalization_failures"
"""컬럼별 정규화 실패 값 수(dict[str, int])를 올리는 stage_artifacts 키."""

AMBIGUOUS_NOTE = (
    "상위 두 후보의 격차가 임계치 미달이라 LLM 확인 대상이다(--no-llm에서는 최상위 후보로 확정했다)"
)

CURRENCY_DECIMAL_SHAPE = "^[₩\\s]*[+-]?(\\d{1,3}(,\\d{3})+|\\d+)(\\.\\d+)?[원]?[\\s]*$"
"""탐지기(builtin._DECIMAL)와 같은 통화·콤마 허용 형태(위치 포함).

정규화·전수 산정이 이 기준을 공유한다(재검토 3차). 기호를 위치와 상관없이
지우면 '1,5'→15, '12원34'→1234처럼 값이 조용히 바뀐다.
"""


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _missing_predicate(column_id: str, markers: frozenset[str] | set[str]) -> str:
    """결측 판정 SQL 조각(리터럴). S2와 같은 규칙 — 마커는 설정 상수다."""
    quoted = quote_identifier(column_id)
    clauses = [f"{quoted} IS NULL", f"regexp_matches({quoted}, '^\\s*$')"]
    clauses += [f"{quoted} = {_sql_literal(marker)}" for marker in sorted(markers)]
    return " OR ".join(f"({clause})" for clause in clauses)


def _decimal_cast_expression(quoted: str, scale: int) -> str:
    """탐지기와 같은 통화 형태를 통과한 값만 decimal로 옮긴다.

    기호 제거는 형태 검사를 통과한 값에만 적용한다(재검토 3차). scale은
    전수 최대 소수 자릿수다(재검토 2차). 정수부는 38-scale자리까지만 허용해
    초과값은 NULL(실패)로 둔다 — DECIMAL(38, s) 캐스팅은 s를 넘는 소수부를
    반올림하므로 형태 검사로 먼저 거른다.
    """
    cleaned = f"regexp_replace({quoted}, '[₩원,\\s]', '', 'g')"
    if scale <= 0:
        shape = "'^[+-]?\\d{1,38}$'"
    else:
        shape = f"'^[+-]?\\d{{1,{38 - scale}}}(\\.\\d{{1,{scale}}})?$'"
    return (
        f"CASE WHEN regexp_matches({quoted}, '{CURRENCY_DECIMAL_SHAPE}') "
        f"AND regexp_matches({cleaned}, {shape}) "
        f"THEN TRY_CAST({cleaned} AS DECIMAL(38, {scale})) ELSE NULL END"
    )


def _normalize_expression(
    column_id: str,
    rule: NormalizationRule,
    pad_width: int,
    decimal_scale: int = 10,
) -> str:
    """규칙별 값 변환 SQL(결측 → NULL 포장은 run에서 한다).

    무손실 원칙: 값을 그대로 옮기거나 NULL(실패)로 둔다. 반올림·절단·임의
    보정은 하지 않는다(재검토 2차). pad_width·decimal_scale은 전수 최댓값이다.
    """
    quoted = quote_identifier(column_id)
    if rule is NormalizationRule.IDENTITY:
        inner = quoted
    elif rule is NormalizationRule.INTEGER:
        # 엄격한 정수 형태만 변환한다. DuckDB의 TRY_CAST('5.5' AS BIGINT)는
        # 실패하지 않고 6으로 반올림한다(재검토 지적) — 소수·지수 표기는
        # NULL로 두고 정규화 실패로 센다.
        inner = (
            f"CASE WHEN regexp_matches({quoted}, '^\\s*[+-]?\\d+\\s*$') "
            f"THEN TRY_CAST({quoted} AS BIGINT) ELSE NULL END"
        )
    elif rule is NormalizationRule.DECIMAL:
        inner = _decimal_cast_expression(quoted, decimal_scale)
    elif rule is NormalizationRule.FLOAT:
        inner = f"TRY_CAST({quoted} AS DOUBLE)"
    elif rule is NormalizationRule.BOOLEAN:
        inner = (
            f"CASE WHEN lower({quoted}) IN ('y', '예', 'o', 'true') THEN TRUE "
            f"WHEN lower({quoted}) IN ('n', '아니오', 'x', 'false') THEN FALSE "
            "ELSE NULL END"
        )
    elif rule is NormalizationRule.DATE:
        parsed = ", ".join(
            f"CAST(TRY_STRPTIME({quoted}, '{fmt}') AS DATE)"
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")
        )
        # 시리얼 분기도 정수 형태일 때만 — TRY_CAST INTEGER는 소수를 반올림한다.
        serial = (
            f"CASE WHEN regexp_matches({quoted}, '^\\s*\\d+\\s*$') "
            f"AND TRY_CAST({quoted} AS INTEGER) BETWEEN {EXCEL_SERIAL_MIN} "
            f"AND {EXCEL_SERIAL_MAX} THEN DATE '1899-12-30' + "
            f"TRY_CAST({quoted} AS INTEGER) ELSE NULL END"
        )
        inner = f"COALESCE({parsed}, {serial})"
    elif rule is NormalizationRule.TIMESTAMP:
        inner = f"TRY_CAST({quoted} AS TIMESTAMP)"
    elif rule is NormalizationRule.TIME:
        inner = f"TRY_CAST({quoted} AS TIME)"
    else:  # CODE_PAD — 폭은 전수 최대 길이라 잘리는 값이 없다.
        inner = (
            f"CASE WHEN regexp_matches({quoted}, '^\\d+$') "
            f"THEN LPAD({quoted}, {pad_width}, '0') ELSE {quoted} END"
        )
    return inner


def _hint_bonus(detector: Detector, column: str) -> float:
    if any(hint_matches(column, family) for family in detector.hint_families):
        return HINT_BONUS
    return 0.0


def _sample_values(
    ctx: AnalysisContext,
    table: str,
    column_id: str,
    predicate: str,
    limit: int,
    seed: int,
) -> tuple[str, ...]:
    """판정 표본 — 시드 고정 무작위 표본(앞쪽 편향 금지, 재검토 #2).

    결측 아닌 값을 모은 subquery에 DuckDB reservoir 표본을 얹는다. 같은
    시드는 같은 표본을 낸다.
    """
    quoted = quote_identifier(column_id)
    rows = ctx.connection.execute(
        f"SELECT {quoted} FROM (SELECT {quoted} FROM {table} "
        f"WHERE NOT ({predicate})) USING SAMPLE {limit} ROWS (reservoir, {seed})"
    ).fetchall()
    return tuple(row[0] for row in rows if isinstance(row[0], str))


def _full_decimal_shape(ctx: AnalysisContext, predicate: str, column_id: str) -> tuple[int, int]:
    """전수 최대 (정수부 유효 자릿수, 소수부 자릿수) — 통화 형태를 통과한 값 기준.

    표본이 무작위라 표본 최댓값이 실제보다 작을 수 있다(재검토 2차). 이 값이
    TypeAssignment의 precision·scale과 정규화 캐스팅 scale에 그대로 쓰인다.
    형태는 정규화와 같은 CURRENCY_DECIMAL_SHAPE다(재검토 3차) — '12원34' 같은
    값은 기호를 떼도 12·34 자릿수 산정에 넣지 않는다.
    """
    quoted = quote_identifier(column_id)
    cleaned = f"regexp_replace({quoted}, '[₩원,\\s]', '', 'g')"
    currency = f"regexp_matches({quoted}, '{CURRENCY_DECIMAL_SHAPE}')"
    decimal_shape = f"regexp_matches({cleaned}, '^[+-]?\\d+(\\.\\d+)?$')"
    significant = "ltrim(ltrim(split_part(c, '.', 1), '+-'), '0')"
    row = ctx.connection.execute(
        f"SELECT max(CASE WHEN length({significant}) = 0 THEN 1 "
        f"ELSE length({significant}) END), "
        f"max(length(split_part(c, '.', 2))) FROM "
        f"(SELECT {cleaned} AS c FROM {RAW_TABLE} "
        f"WHERE NOT ({predicate}) AND {currency} AND {decimal_shape})"
    ).fetchone()
    if row is None or row[0] is None:
        return 1, 0
    return int(row[0]), int(row[1] or 0)


def _full_digit_width(ctx: AnalysisContext, predicate: str, column_id: str) -> int:
    """숫자 코드의 전수 최대 길이 — 폭보다 긴 값이 없어 LPAD가 자르지 않는다."""
    quoted = quote_identifier(column_id)
    row = ctx.connection.execute(
        f"SELECT max(length({quoted})) FROM {RAW_TABLE} "
        f"WHERE NOT ({predicate}) AND regexp_matches({quoted}, '^\\d+$')"
    ).fetchone()
    if row is None or row[0] is None:
        return 1
    return max(int(row[0]), 1)


def _rule_parameters(
    ctx: AnalysisContext, predicate: str, column_id: str, guess: TypeGuess
) -> tuple[str, int | None, int | None]:
    """확정 후보의 (정규화 식, precision, scale). 전수 최댓값을 반영한다."""
    if guess.normalization is NormalizationRule.DECIMAL:
        int_digits, frac_digits = _full_decimal_shape(ctx, predicate, column_id)
        # scale 38이면 정수부 허용 폭(38-scale)이 0이 돼 형태 정규식이
        # 무효하다({1,0}). 상한 37로 두고 넘는 소수부는 NULL(실패)로 둔다.
        scale = min(frac_digits, 37)
        precision = min(int_digits + scale, 38)
        return (
            _normalize_expression(column_id, guess.normalization, 0, scale),
            precision,
            scale,
        )
    if guess.normalization is NormalizationRule.CODE_PAD:
        width = _full_digit_width(ctx, predicate, column_id)
        return _normalize_expression(column_id, guess.normalization, width), None, None
    return (
        _normalize_expression(column_id, guess.normalization, guess.pad_width),
        guess.precision,
        guess.scale,
    )


def _rank(
    guesses: list[tuple[Detector, TypeGuess]], column: str
) -> list[tuple[Detector, TypeGuess]]:
    """(적합률 + 힌트 보너스, 우선순위, 탐지기 이름) 내림차순. 결정론 정렬."""
    return sorted(
        guesses,
        key=lambda item: (
            -(item[1].confidence + _hint_bonus(item[0], column)),
            -item[0].priority,
            item[0].name,
        ),
    )


def run(
    ctx: AnalysisContext,
    ingest: IngestManifest,
    profiles: list[ColumnProfile],
    *,
    detectors: tuple[Detector, ...] = BUILTIN_DETECTORS,
    missing_markers: frozenset[str] | set[str] = DEFAULT_MISSING_MARKERS,
    fit_threshold: float = FIT_THRESHOLD,
    ambiguous_gap: float = AMBIGUOUS_GAP,
    sample_limit: int = DETECT_SAMPLE_LIMIT,
    seed: int = DEFAULT_SEED,
) -> list[TypeAssignment]:
    """컬럼별 물리·의미 타입을 확정하고 정규화 뷰를 컨텍스트에 남긴다."""
    profile_by_name = {profile.column: profile for profile in profiles}
    sample_relation = ctx.stage_artifacts.get(SAMPLE_RELATION_KEY)
    profile_table = sample_relation if isinstance(sample_relation, str) else RAW_TABLE

    assignments: list[TypeAssignment] = []
    view_columns: list[str] = []
    failures: dict[str, int] = {}

    for column in ingest.columns:
        column_id = ctx.column_id_of(column.index)
        predicate = _missing_predicate(column_id, missing_markers)
        values = _sample_values(ctx, profile_table, column_id, predicate, sample_limit, seed)
        sample = ColumnSample(
            column=column.name, values=values, profile=profile_by_name[column.name]
        )

        candidates = [
            (detector, guess)
            for detector in detectors
            if (guess := detector.detect(sample)) is not None
        ]
        passing = [pair for pair in candidates if pair[1].confidence >= fit_threshold]
        # 임계치를 넘은 후보가 없으면(커스텀 탐지기 목록 등) 최선의 후보로 확정한다.
        ranked = _rank(passing if passing else candidates, column.name)
        if not ranked:
            # 어떤 탐지기도 후보를 내지 않은 경우(빈 커스텀 목록) — 문자열로 둔다.
            assignments.append(
                TypeAssignment(
                    column=column.name,
                    physical_type=PhysicalType.STRING,
                    semantic_type=SemanticType.FREE_TEXT,
                    detector="builtin.fallback",
                    confidence=0.0,
                )
            )
            view_columns.append(
                f"CASE WHEN {predicate} THEN NULL ELSE {quote_identifier(column_id)} END "
                f"AS {quote_identifier(column_id)}"
            )
            failures[column.name] = 0
            continue

        # 전수 확인(재검토 지적): 표본은 무작위라도 전부를 보지 않으면 뒤늦게
        # 나오는 이형 값을 놓친다. 최상위 후보의 전수 실패 비율(값이 있는데
        # 정규화 결과가 NULL)이 1 - 임계치를 넘으면 다음 후보로 내려가 같은
        # 확인을 한다. 어느 후보도 못 넘으면 전수 실패가 가장 적은 후보로
        # 확정한다.
        nonmissing = profile_by_name[column.name].count
        confirmation: list[tuple[int, int, Detector, TypeGuess, str, int | None, int | None]] = []
        adopted = False
        for detector, guess in ranked:
            # 정밀도·자릿수·폭은 전수 최댓값으로 정한다(재검토 2차).
            inner, precision, scale = _rule_parameters(ctx, predicate, column_id, guess)
            row = ctx.connection.execute(
                f"SELECT COUNT(*) FROM {RAW_TABLE} WHERE NOT ({predicate}) AND ({inner}) IS NULL"
            ).fetchone()
            full_failures = int(row[0]) if row is not None else 0
            confirmation.append(
                (full_failures, -detector.priority, detector, guess, inner, precision, scale)
            )
            if nonmissing == 0 or full_failures <= (1 - fit_threshold) * nonmissing:
                adopted = True
                break
        if adopted:
            # 통과한 후보(마지막에 시도한 것)로 확정한다(재검토 3차). 탈락한
            # 첫 후보를 다시 고르면 하향이 실제로 작동하지 않는다.
            chosen = confirmation[-1]
        else:
            confirmation.sort(key=lambda item: (item[0], item[1], item[2].name))
            chosen = confirmation[0]
        failure_count, _, top_detector, top, inner, precision, scale = chosen

        notes: list[str] = []
        others = [pair for pair in ranked if pair[0] is not top_detector]
        if others:
            chosen_score = top.confidence + _hint_bonus(top_detector, column.name)
            second_score = others[0][1].confidence + _hint_bonus(others[0][0], column.name)
            if chosen_score - second_score < ambiguous_gap:
                notes.append(AMBIGUOUS_NOTE)
        alternatives = [
            TypeAlternative(semantic_type=guess.semantic_type, confidence=guess.confidence)
            for _, guess in ranked[1:3]
        ]
        if failure_count:
            notes.append(f"정규화 실패 {failure_count}값")

        assignments.append(
            TypeAssignment(
                column=column.name,
                physical_type=top.physical_type,
                semantic_type=top.semantic_type,
                precision=precision,
                scale=scale,
                detector=top_detector.name,
                confidence=top.confidence,
                alternatives=alternatives,
                notes=notes,
            )
        )
        failures[column.name] = failure_count
        view_columns.append(
            f"CASE WHEN {predicate} THEN NULL ELSE {inner} END AS {quote_identifier(column_id)}"
        )

    ctx.connection.execute(
        f"CREATE OR REPLACE VIEW {NORMALIZED_VIEW} AS "
        f"SELECT {', '.join(view_columns)} FROM {RAW_TABLE}"
    )
    ctx.normalized_view = NORMALIZED_VIEW
    ctx.stage_artifacts[FAILURES_KEY] = failures
    return assignments
