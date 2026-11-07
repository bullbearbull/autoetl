"""S5 관계 분석 — 후보 키·근사 함수 종속·상관(T-0026).

모든 계산은 S3의 정규화 뷰(ctx.normalized_view) 위에서 한다(리드 결정). 원문
값으로 하면 형식 혼재·앞자리 0 때문에 유일성과 함수 종속이 깨진다. 결측·상수
판정도 뷰 기준이다 — S3 정규화 실패로 NULL이 된 값(원문 결측 아님)이 있는
컬럼은 키가 될 수 없다.

- 후보 키: 결측 없는 비상수 컬럼 조합(기본 최대 3컬럼)의 유일성. 상수 컬럼과
  거의 유일한 컬럼(고유 비율 0.95 이상·유일 아님 — unit_price류)은 복합 조합에서
  뺀다. 유일한 조합의 상위 조합은 만들지 않는다(최소성 + 폭발 제어).
- 근사 FD: 좌변 크기 기본 2 이하(계약 상한 3까지 설정 가능). 좌변·우변의 결측
  행은 위반에서 뺀다. 채택은 행 기준(위반 행 수 ÷ 우변 관측 행 수 ≤ 1%)이고
  근사(위반 > 0)는 좌변 평균 그룹 크기가 2 이상일 때만 받는다 — 거의 유일한
  좌변은 그룹이 대부분 한 행이라 구조상 근사 FD가 된다. support는 'lhs 값 중
  rhs가 유일한 비율'(그룹 기준, 계약 설명), violation_count는 위반 lhs 값의
  행 수다. 채택한 FD는 전수 GROUP BY로 확정했으므로 verification=full이다.
- 제거 규칙(리드 검토 #168): 진부분집합 좌변이 같은 우변을 정확히 결정하면
  제거(최소 좌변). 키 좌변 FD는 대표 키(크기 → S3 타입 랭크 identifier·
  integer > code > 그 밖 → 컬럼 순서) 하나에서만 낸다. 동치 컬럼(서로 정확히
  결정하는 단일 컬럼)도 같은 대표 규칙으로 대표만 결정자다. 이행 제거는 대표들
  사이에 일반 적용(a→b·b→c가 정확하고 b·c가 동치가 아니면 a→c 제거).
- 포함 종속: 단일 파일 입력에서는 빈 목록이다(다중 파일 입력 때).
- 상관: 수치 쌍은 Pearson·Spearman(동률은 평균 순위 — 파티션 ROW_NUMBER
  평균, 12자리 반올림로 병렬 집계 흔들림 차단), 범주 쌍은 Cramér's V.
  상호정보량·파생·계층은 이번 범위 밖이다(빈 목록 + notes).
- 폭발 제어: 카디널리티 커트(card(rhs) ≤ card(lhs) + 허용 위반 행 수), 좌변
  하나당 쿼리 하나(우변 전체 동시 측정), 키·FD 각각 작업량 예산(검증 조합
  수 — 벽시계가 아니라 결정론). 예산 초과·제외 컬럼은 notes에 남긴다. SQL에는
  내부 식별자(c0…)만 들어가고(단계 규약) 집계 숫자만 다룬다.
"""

from __future__ import annotations

import math
from itertools import combinations

from autoetl.contracts import (
    CandidateKey,
    ColumnProfile,
    Correlation,
    CorrelationMethod,
    FunctionalDependency,
    IngestManifest,
    PhysicalType,
    RelationGraph,
    SemanticType,
    TypeAssignment,
    VerificationScope,
)
from autoetl.core.context import AnalysisContext, quote_identifier

MAX_KEY_COLUMNS = 3
"""후보 키 조합의 최대 컬럼 수(계약 상한)."""

MAX_LHS = 2
"""FD 좌변 기본 최대 크기(계약 상한 3까지 설정 가능)."""

FD_VIOLATION_RATE = 0.01
"""근사 FD 채택 위반률 상한(행 기준)."""

KEY_BUDGET = 1000
"""키 검증 조합 수 예산(작업량 기준 — 결정론)."""

LHS_BUDGET = 1000
"""FD 검증 좌변 후보 수 예산(작업량 기준 — 결정론)."""

NEAR_UNIQUE_RATIO = 0.95
"""거의 유일 컬럼 판정 비율(이상·유일 미만) — 복합 키·좌변 조합에서 뺀다."""

MIN_AVG_GROUP = 2.0
"""근사 FD(위반 > 0)를 받는 최소 좌변 평균 그룹 크기(행 수 ÷ lhs 고유값 수)."""

CATEGORY_CARDINALITY_LIMIT = 20
"""Cramér's V를 계산할 범주 컬럼의 고유값 상한."""

NUMERIC_TYPES = frozenset({PhysicalType.INTEGER, PhysicalType.DECIMAL, PhysicalType.FLOAT})
CATEGORY_TYPES = frozenset({PhysicalType.STRING, PhysicalType.BOOLEAN})


def _type_rank(assignment: TypeAssignment, position: int) -> tuple[int, int]:
    """동치·키 대표 선정 우선순위(리드 #168): 정수(identifier·integer) > 코드 >
    그 밖, 같으면 컬럼 순서. 낮을수록 좋다."""
    if assignment.semantic_type is SemanticType.IDENTIFIER or (
        assignment.semantic_type is not SemanticType.CODE
        and assignment.physical_type is PhysicalType.INTEGER
    ):
        rank = 0
    elif assignment.semantic_type is SemanticType.CODE:
        rank = 1
    else:
        rank = 2
    return (rank, position)


def _is_unique(ctx: AnalysisContext, view: str, quoted: list[str]) -> bool:
    """조합 값이 전부 유일한지(중복 그룹이 없는지). EXISTS로 조기 종료한다.

    후보 키는 유일한 조합만 담으니(violation_count 0) 위반 행 수는 세지
    않는다 — 중복이 하나라도 보이면 바로 돌아온다(대부분의 조합이 이 경우).
    """
    group = ", ".join(quoted)
    row = ctx.connection.execute(
        f"SELECT EXISTS (SELECT 1 FROM {view} GROUP BY {group} HAVING COUNT(*) > 1)"
    ).fetchone()
    return not bool(row[0]) if row is not None else False


def _fd_measures(
    ctx: AnalysisContext, view: str, lhs_quoted: list[str], rhs_quoted: list[str]
) -> tuple[float, dict[str, tuple[int, int, int, int]]]:
    """한 좌변의 평균 그룹 크기와 모든 우변의 (위반 lhs 그룹 수, lhs 그룹 수,
    위반 행 수, 우변 관측 행 수)를 한 쿼리로 잰다.

    우변 결측 행은 COUNT(우변)으로 빠진다(위반 아님). 좌변 결측 행은 WHERE로
    뺀다. 채택은 행 기준(위반 행 ÷ 관측 행), 근사 게이트는 평균 그룹 크기다.
    """
    aliases = [f"r{i}" for i in range(len(rhs_quoted))]
    lhs_group = ", ".join(lhs_quoted)
    lhs_not_null = " AND ".join(f"{column} IS NOT NULL" for column in lhs_quoted)
    aggregates = ", ".join(
        f"COUNT({column}) AS {alias}_rows, COUNT(DISTINCT {column}) AS {alias}_cnt"
        for column, alias in zip(rhs_quoted, aliases, strict=True)
    )
    selects = (
        "COALESCE(SUM(n) / COUNT(*), 0), "  # 평균 그룹 크기(행 수 ÷ lhs 그룹 수)
        + ", ".join(
            f"COALESCE(SUM(CASE WHEN {alias}_cnt > 1 THEN 1 END), 0), COUNT(*), "
            f"COALESCE(SUM(CASE WHEN {alias}_cnt > 1 THEN {alias}_rows END), 0), "
            f"COALESCE(SUM({alias}_rows), 0)"
            for alias in aliases
        )
    )
    row = ctx.connection.execute(
        f"SELECT {selects} FROM "
        f"(SELECT {lhs_group}, COUNT(*) AS n, {aggregates} FROM {view} "
        f"WHERE {lhs_not_null} GROUP BY {lhs_group})"
    ).fetchone()
    if row is None:
        return 0.0, {}
    measures = {
        column: (
            int(row[i * 4 + 1] or 0),
            int(row[i * 4 + 2] or 0),
            int(row[i * 4 + 3] or 0),
            int(row[i * 4 + 4] or 0),
        )
        for i, column in enumerate(rhs_quoted)
    }
    return float(row[0] or 0), measures


def _finite(value: object) -> float | None:
    """corr 결과를 [-1, 1]로 묶고 12자리로 반올림한다.

    병렬 집계의 부동소수 합산 순서가 실행마다 값을 끝자리(1e-16)에서 흔든다 —
    같은 입력은 같은 그래프(완료 기준)를 지키려면 자리를 줄인다.
    """
    if isinstance(value, float) and math.isfinite(value):
        return round(max(-1.0, min(1.0, value)), 12)
    return None


def _pearson(ctx: AnalysisContext, view: str, a: str, b: str) -> float | None:
    row = ctx.connection.execute(f"SELECT corr({a}, {b}) FROM {view}").fetchone()
    return _finite(row[0]) if row is not None else None


def _spearman(ctx: AnalysisContext, view: str, a: str, b: str) -> float | None:
    """순위 상관. 동률은 평균 순위로 푼다(파티션 ROW_NUMBER 평균).

    RANK()는 동률에 최소 순위를 줘서 평균을 내도 그대로다(리드 #168) — 값
    파티션의 ROW_NUMBER 평균이 표준 평균 순위다.
    """
    row = ctx.connection.execute(
        f"WITH pairs AS (SELECT {a} AS x, {b} AS y FROM {view} "
        f"WHERE {a} IS NOT NULL AND {b} IS NOT NULL), "
        f"xr AS (SELECT AVG(rn) OVER (PARTITION BY x) AS rx, y FROM "
        f"(SELECT x, y, ROW_NUMBER() OVER (ORDER BY x) AS rn FROM pairs)), "
        f"yr AS (SELECT rx, AVG(rn) OVER (PARTITION BY y) AS ry FROM "
        f"(SELECT rx, y, ROW_NUMBER() OVER (ORDER BY y) AS rn FROM xr)) "
        f"SELECT corr(rx, ry) FROM yr"
    ).fetchone()
    return _finite(row[0]) if row is not None else None


def _cramers_v(ctx: AnalysisContext, view: str, a: str, b: str) -> float | None:
    """범주 쌍의 Cramér's V. 교차표 집계만 가져와 파이썬에서 카이제곱을 잰다.

    관측이 0인 칸도 카이제곱에 들어간다(기대빈도만 있는 칸) — GROUP BY
    결과엔 나오지 않는 칸을 합계로 다시 만든다.
    """
    rows = ctx.connection.execute(
        f"SELECT {a}, {b}, COUNT(*) FROM {view} "
        f"WHERE {a} IS NOT NULL AND {b} IS NOT NULL GROUP BY {a}, {b}"
    ).fetchall()
    if not rows:
        return None
    row_totals: dict[object, int] = {}
    col_totals: dict[object, int] = {}
    observed: dict[tuple[object, object], int] = {}
    total = 0
    for row in rows:
        a_value, b_value, count = row[0], row[1], int(row[2])
        observed[(a_value, b_value)] = count
        row_totals[a_value] = row_totals.get(a_value, 0) + count
        col_totals[b_value] = col_totals.get(b_value, 0) + count
        total += count
    if len(row_totals) < 2 or len(col_totals) < 2:
        return None  # 한쪽이 단일 값이면 관계 구분이 불가능하다
    chi2 = sum(
        (observed.get((a_value, b_value), 0) - row_totals[a_value] * col_totals[b_value] / total)
        ** 2
        / (row_totals[a_value] * col_totals[b_value] / total)
        for a_value in row_totals
        for b_value in col_totals
    )
    v = math.sqrt(chi2 / (total * (min(len(row_totals), len(col_totals)) - 1)))
    return min(v, 1.0)


def _minimal_lhs(fds: list[FunctionalDependency]) -> list[FunctionalDependency]:
    """진부분집합 좌변이 같은 우변을 '정확히'(위반 0) 결정하면 제거한다.

    근사 FD는 위반 수가 좌변마다 달라 도출 보장이 없다 — 정확할 때만 최소
    좌변 원칙을 적용한다.
    """
    kept: list[FunctionalDependency] = []
    for fd in fds:
        lhs_set = set(fd.lhs)
        if any(
            other.violation_count == 0 and other.rhs == fd.rhs and set(other.lhs) < lhs_set
            for other in fds
        ):
            continue
        kept.append(fd)
    return kept


def _equivalence_classes(fds: list[FunctionalDependency]) -> dict[str, str]:
    """서로 정확히 결정하는 단일 컬럼 쌍을 동치 클래스로 묶어 대표를 정한다.

    대표는 S3 타입 랭크(identifier·integer > code > 그 밖) → 컬럼 순서.
    반환은 (컬럼 → 대표) 맵. 대표가 아닌 컬럼은 대표와의 상호 FD만 남긴다.
    """
    exact = {(fd.lhs[0], fd.rhs) for fd in fds if len(fd.lhs) == 1 and fd.violation_count == 0}
    parent: dict[str, str] = {}

    def find(name: str) -> str:
        root = name
        while parent.get(root, root) != root:
            root = parent[root]
        return root

    def union(a: str, b: str) -> None:
        for name in (a, b):
            parent.setdefault(name, name)
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in exact:
        if (b, a) in exact:  # 양방향 정확 성립 = 동치
            union(a, b)
    return {name: find(name) for name in parent}


def _drop_transitive(
    fds: list[FunctionalDependency], equivalent: dict[str, str]
) -> list[FunctionalDependency]:
    """이행(a→b·b→c가 정확하고 b·c가 동치가 아니면 a→c 제거)을 일반 적용한다.

    동치 쌍을 경유하면 a→c는 다른 정보라 지우지 않는다(FK 직접 종속 보존).
    근사 경로는 도출 보장이 없어 자격이 없다.
    """
    exact = {(fd.lhs[0], fd.rhs) for fd in fds if len(fd.lhs) == 1 and fd.violation_count == 0}
    middles = {a for a, _ in exact}
    kept: list[FunctionalDependency] = []
    for fd in fds:
        if len(fd.lhs) == 1:
            a, c = fd.lhs[0], fd.rhs
            if any(
                b != a
                and b != c
                and (a, b) in exact
                and (b, c) in exact
                and equivalent.get(b, b) != equivalent.get(c, c)
                for b in middles
            ):
                continue
        kept.append(fd)
    return kept


def _correlations(
    ctx: AnalysisContext,
    view: str,
    quoted: dict[str, str],
    numeric: list[str],
    categoric: list[str],
) -> list[Correlation]:
    result: list[Correlation] = []
    for a, b in combinations(sorted(numeric), 2):
        qa, qb = quoted[a], quoted[b]
        for method, value in (
            (CorrelationMethod.PEARSON, _pearson(ctx, view, qa, qb)),
            (CorrelationMethod.SPEARMAN, _spearman(ctx, view, qa, qb)),
        ):
            if value is not None:
                result.append(Correlation(a=a, b=b, method=method, value=value))
    for a, b in combinations(sorted(categoric), 2):
        value = _cramers_v(ctx, view, quoted[a], quoted[b])
        if value is not None:
            result.append(Correlation(a=a, b=b, method=CorrelationMethod.CRAMERS_V, value=value))
    result.sort(key=lambda c: (c.a, c.b, c.method.value))
    return result


def _remaining_combinations(pool_size: int, from_size: int, max_size: int) -> int:
    """남은 조합 수를 조합 공식으로 계산한다(예산 초과 notes용 근사)."""
    return sum(math.comb(pool_size, size) for size in range(from_size, max_size + 1))


def run(
    ctx: AnalysisContext,
    ingest: IngestManifest,
    types: list[TypeAssignment],
    profiles: list[ColumnProfile],
    *,
    max_key_columns: int = MAX_KEY_COLUMNS,
    max_lhs: int = MAX_LHS,
    fd_violation_rate: float = FD_VIOLATION_RATE,
    category_cardinality_limit: int = CATEGORY_CARDINALITY_LIMIT,
    key_budget: int = KEY_BUDGET,
    lhs_budget: int = LHS_BUDGET,
) -> RelationGraph:
    """정규화 뷰 위에서 후보 키·근사 FD·상관을 잰다. 같은 입력은 같은 그래프다."""
    if max_lhs > 3 or max_key_columns > 3:
        raise ValueError("좌변·키 조합은 계약 상한(3컬럼)을 넘을 수 없다")
    view = ctx.normalized_view
    if view is None:
        raise ValueError("S3 정규화 뷰가 없다 — S5는 S3 다음에만 돈다")

    type_by = {assignment.column: assignment for assignment in types}
    quoted = {
        column.name: quote_identifier(ctx.column_id_of(column.index)) for column in ingest.columns
    }
    names = [column.name for column in ingest.columns]
    position = {name: index for index, name in enumerate(names)}
    notes: list[str] = []

    # 뷰 기준 통계(행 수·결측 아닌 값 수·고유값 수): 결측·상수 판정도 뷰에서
    # 한다 — S3 정규화 실패로 NULL이 된 값(원문 결측 아님)이 있는 컬럼이
    # 키가 되는 것을 막는다(리드 #168). S2 프로파일은 원문 기준이라 쓰지
    # 않는다. 카디널리티 커트도 이 값으로 한다.
    total_row = ctx.connection.execute(f"SELECT COUNT(*) FROM {view}").fetchone()
    row_total = int(total_row[0] or 0) if total_row is not None else 0
    non_null: dict[str, int] = {}
    cardinality: dict[str, int] = {}
    for name in names:
        stat_row = ctx.connection.execute(
            f"SELECT COUNT({quoted[name]}), COUNT(DISTINCT {quoted[name]}) FROM {view}"
        ).fetchone()
        non_null[name] = int(stat_row[0] or 0) if stat_row is not None else 0
        cardinality[name] = int(stat_row[1] or 0) if stat_row is not None else 0

    # 상수 컬럼은 좌변·우변 모두에서 뺀다(어떤 좌변이든 결정한다 — FD 폭발).
    # 결측이 있는 컬럼은 키·좌변 후보에서 뺀다. 제외 컬럼 이름은 notes에 남긴다.
    constants = sorted(name for name in names if cardinality[name] <= 1)
    if constants:
        notes.append("상수 컬럼은 좌변·우변 후보에서 뺐다: " + ", ".join(constants))
    missing_cols = sorted(name for name in names if non_null[name] < row_total)
    if missing_cols:
        notes.append(
            "결측이 있는 컬럼(정규화 뷰 기준)은 키·좌변 후보에서 뺐다: " + ", ".join(missing_cols)
        )
    # 키·좌변 후보: 결측 없는 비상수 컬럼. 우변은 상수가 아닌 컬럼 전부.
    nonconstant = sorted(
        name for name in names if non_null[name] == row_total and cardinality[name] > 1
    )
    rhs_pool = sorted(name for name in names if cardinality[name] > 1)
    # 거의 유일한 컬럼(0.95 이상·유일 아님)은 복합 조합에서 뺀다 — 붙이면
    # 대부분 유일해져 우연 키·FD가 쏟아진다.
    near_unique = sorted(
        name for name in nonconstant if NEAR_UNIQUE_RATIO <= cardinality[name] / row_total < 1.0
    )
    if near_unique:
        notes.append("거의 유일한 컬럼은 복합 키·좌변 조합에서 뺐다: " + ", ".join(near_unique))
    compound_pool = [name for name in nonconstant if name not in near_unique]

    # ── 후보 키 ──────────────────────────────────────────────────────
    # 카디널리티 상한 커트(정확한 필요조건): 조합 distinct는 각 컬럼 고유값
    # 수의 곱 이하라, 곱이 행 수에 못 미치면 유일이 불가능하다.
    keys: list[CandidateKey] = []
    unique_single: set[str] = set()
    key_columns: set[frozenset[str]] = set()
    key_verified = 0
    key_budget_hit: int | None = None
    for size in range(1, max_key_columns + 1):
        pool = nonconstant if size == 1 else compound_pool
        for combo in combinations(pool, size):
            combo_set = frozenset(combo)
            # 유일 단일 컬럼을 포함한 복합은 단일로 충분하다(최소성·폭발 제어).
            if size > 1 and unique_single & combo_set:
                continue
            if any(part < combo_set for part in key_columns):
                continue
            combo_cardinality = 1
            for name in combo:
                combo_cardinality *= cardinality[name]
            if combo_cardinality < row_total:
                continue
            if key_verified >= key_budget:
                key_budget_hit = size
                break
            key_verified += 1
            if _is_unique(ctx, view, [quoted[name] for name in combo]):
                keys.append(CandidateKey(columns=list(combo), unique=True, violation_count=0))
                key_columns.add(combo_set)
                if size == 1:
                    unique_single.add(combo[0])
        if key_budget_hit is not None:
            break
    keys.sort(key=lambda key: (len(key.columns), key.columns))
    if key_budget_hit is not None:
        remaining = _remaining_combinations(len(compound_pool), key_budget_hit, max_key_columns)
        notes.append(
            f"키 조합 예산({key_budget}) 초과 — 크기 {key_budget_hit - 1}까지 완료, "
            f"남은 조합 약 {remaining}개"
        )
    # 대표 키(리드 #168): 크기 → 구성 컬럼 타입 랭크 최댓값 → 컬럼 순서.
    # 키 좌변 FD는 대표 하나에서만 낸다.
    representative_key = min(
        (key for key in keys if all(name in type_by for name in key.columns)),
        key=lambda key: (
            len(key.columns),
            max(_type_rank(type_by[name], position[name]) for name in key.columns),
            [position[name] for name in key.columns],
        ),
        default=None,
    )

    # ── 근사 함수 종속(2패스) ─────────────────────────────────────────
    # 패스 1: 단일 좌변 전부를 잰다(동치 클래스를 알기 전이라 제한 없이) —
    # 좌변 수만큼만 쿼리한다. 카디널리티 커트: card(rhs) ≤ card(lhs) + 허용
    # 위반 행 수(행 기준 채택의 필요조건).
    allowed_violation_rows = fd_violation_rate * row_total
    fds: list[FunctionalDependency] = []
    exact_single: set[tuple[str, str]] = set()
    lhs_verified = 0
    lhs_budget_hit: int | None = None

    def _adopt(
        lhs: tuple[str, ...],
        avg_group: float,
        measures: dict[str, tuple[int, int, int, int]],
        targets: list[str],
    ) -> None:
        for name in targets:
            bad_groups, total_groups, bad_rows, rows_total = measures[quoted[name]]
            if rows_total == 0:
                continue
            if bad_rows > fd_violation_rate * rows_total:  # 채택은 행 기준
                continue
            if bad_groups > 0 and avg_group < MIN_AVG_GROUP:  # 근사 게이트
                continue
            fds.append(
                FunctionalDependency(
                    lhs=list(lhs),
                    rhs=name,
                    support=1.0 - bad_groups / total_groups,
                    violation_count=bad_rows,
                    verification=VerificationScope.FULL,
                )
            )
            if len(lhs) == 1 and bad_groups == 0:
                exact_single.add((lhs[0], name))

    for name in nonconstant:  # 패스 1: 단일 lhs
        if lhs_verified >= lhs_budget:
            lhs_budget_hit = 1
            break
        lhs_verified += 1
        targets = [
            rhs
            for rhs in rhs_pool
            if rhs != name and cardinality[rhs] <= cardinality[name] + allowed_violation_rows
        ]
        if not targets:
            continue
        avg_group, measures = _fd_measures(
            ctx, view, [quoted[name]], [quoted[rhs] for rhs in targets]
        )
        _adopt((name,), avg_group, measures, targets)

    # 동치 클래스(서로 정확히 결정하는 단일 컬럼)와 비대표 lhs 규칙.
    equivalent = _equivalence_classes(fds)
    representatives = {
        root: min(
            (name for name in equivalent if equivalent[name] == root),
            key=lambda name: _type_rank(type_by[name], position[name]),
        )
        for root in set(equivalent.values())
    }
    representative_of = {name: representatives[root] for name, root in equivalent.items()}
    fds = [
        fd
        for fd in fds
        if len(fd.lhs) != 1
        or representative_of.get(fd.lhs[0], fd.lhs[0]) == fd.lhs[0]  # 대표만 결정자
        or fd.rhs == representative_of[fd.lhs[0]]  # 비대표는 대표와의 상호 FD만
    ]

    if lhs_budget_hit is None:  # 패스 2: 복합 좌변
        for size in range(2, max_lhs + 1):
            for lhs in combinations(compound_pool, size):
                lhs_set = frozenset(lhs)
                if unique_single & lhs_set:  # 유일 단일 포함 복합은 최소 아님
                    continue
                if (
                    lhs_set in key_columns
                    and representative_key is not None
                    and list(lhs) != representative_key.columns
                ):
                    continue  # 다른 후보 키는 FD를 내지 않는다(대표만 낸다)
                if lhs_verified >= lhs_budget:
                    lhs_budget_hit = size
                    break
                lhs_cardinality = 1
                for name in lhs:
                    lhs_cardinality *= cardinality[name]
                targets = [
                    rhs
                    for rhs in rhs_pool
                    if rhs not in lhs_set
                    and cardinality[rhs] <= lhs_cardinality + allowed_violation_rows
                    and (lhs[0], rhs) not in exact_single
                    and (lhs[-1], rhs) not in exact_single
                ]
                if not targets:
                    continue
                lhs_verified += 1
                avg_group, measures = _fd_measures(
                    ctx, view, [quoted[name] for name in lhs], [quoted[rhs] for rhs in targets]
                )
                _adopt(lhs, avg_group, measures, targets)
            if lhs_budget_hit is not None:
                break
    if lhs_budget_hit is not None:
        remaining = _remaining_combinations(len(compound_pool), lhs_budget_hit, max_lhs)
        notes.append(
            f"좌변 후보 예산({lhs_budget}) 초과 — 크기 {lhs_budget_hit - 1}까지 검증, "
            f"남은 후보 약 {remaining}개"
        )
    fds = _drop_transitive(_minimal_lhs(fds), equivalent)
    fds.sort(key=lambda fd: (len(fd.lhs), fd.lhs, fd.rhs))
    notes.append("상호정보량·파생 컬럼·계층 구조는 이번 범위 밖이다(빈 목록)")

    # ── 상관 ─────────────────────────────────────────────────────────
    numeric = sorted(
        name
        for name in names
        if type_by[name].physical_type in NUMERIC_TYPES and cardinality[name] > 1
    )
    categoric = sorted(
        name
        for name in names
        if type_by[name].physical_type in CATEGORY_TYPES
        and 1 < cardinality[name] <= category_cardinality_limit
    )
    correlations = _correlations(ctx, view, quoted, numeric, categoric)

    return RelationGraph(
        candidate_keys=keys,
        functional_dependencies=fds,
        correlations=correlations,
        notes=notes,
    )
