"""S5 관계 분석 — 후보 키·근사 함수 종속·상관(T-0026).

모든 계산은 S3의 정규화 뷰(ctx.normalized_view) 위에서 한다(리드 결정). 원문
값으로 하면 형식 혼재·앞자리 0 때문에 유일성과 함수 종속이 깨진다.

- 후보 키: 결측 없는 컬럼 조합(기본 최대 3컬럼)의 유일성. 상수 컬럼은 후보에서
  뺀다. 유일한 조합을 진부분집합으로 갖는 상위 조합은 만들지 않는다(최소성 +
  조합 폭발 제어).
- 근사 FD: 좌변 크기 기본 2 이하(계약 상한 3까지 설정 가능). 좌변·우변의 결측
  행은 위반에서 뺀다. support는 'lhs 값 중 rhs가 유일한 비율'(계약 설명)이고
  위반 lhs 값 비율 1% 이하면 채택한다. violation_count는 위반 lhs 값에 속한
  행 수다. 채택한 FD는 전수 GROUP BY로 확정했으므로 verification=full이다.
  진부분집합 좌변이 같은 우변을 정확히 결정하는 FD는 제거한다(최소 좌변).
  키 lhs의 이행(PK→FK→부모 비키)으로 도출되는 FD도 제거한다 — 키가 아닌
  lhs의 직접 종속(예: FK→부모 PK)은 정답 FD라 그대로 둔다. 유일한 단일
  컬럼을 포함한 복합 좌변은 만들지 않고, 동치 키(서로 1-1인 유일 컬럼)는
  대표(컬럼 순서 첫 번째)만 결정자 역할을 한다(리드 결정 #164).
- 포함 종속: 단일 파일 입력에서는 빈 목록이다(파일 간 포함 종속은 다중 파일
  입력 때).
- 상관: 수치 쌍은 Pearson·Spearman, 낮은 카디널리티 범주 쌍은 Cramér's V.
  상호정보량·파생 컬럼·계층 구조는 이번 범위 밖이다(빈 목록 + notes).
- 폭발 제어: 카디널리티 상한 커트(조합 필요조건), 좌변 하나당 쿼리 하나(우변
  전체 동시 측정), 검증 좌변 후보 수 예산(벽시계가 아니라 작업량 — 결정론).
  예산을 넘으면 거기까지의 결과와 남은 후보 수를 notes에 남긴다. 상수·결측
  컬럼 제외도 notes에 남긴다. SQL에는 내부 식별자(c0…)만 들어가고(단계
  규약) 집계 숫자만 다룬다.
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
    TypeAssignment,
    VerificationScope,
)
from autoetl.core.context import AnalysisContext, quote_identifier

MAX_KEY_COLUMNS = 3
"""후보 키 조합의 최대 컬럼 수(계약 상한)."""

MAX_LHS = 2
"""FD 좌변 기본 최대 크기(계약 상한 3까지 설정 가능)."""

FD_VIOLATION_RATE = 0.01
"""근사 FD 채택 위반률 상한."""

LHS_BUDGET = 1000
"""FD 검증 좌변 후보 수 예산(리드 결정 #164).

벽시계 시간이 아니라 검증한 좌변 후보 수로 잰다 — PC 속도에 따라 같은
입력의 그래프가 달라지면 결정론 규칙을 깬다. 기본값은 벤치 두 팩 7변형이
걸리지 않는 넉넉한 크기다(orders_kr ≈ 153, music ≈ 120).
"""

CATEGORY_CARDINALITY_LIMIT = 20
"""Cramér's V를 계산할 범주 컬럼의 고유값 상한."""

NUMERIC_TYPES = frozenset({PhysicalType.INTEGER, PhysicalType.DECIMAL, PhysicalType.FLOAT})
CATEGORY_TYPES = frozenset({PhysicalType.STRING, PhysicalType.BOOLEAN})


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
) -> dict[str, tuple[int, int, int, int]]:
    """한 좌변에서 모든 우변의 (위반 lhs 그룹 수, lhs 그룹 수, 위반 행 수,
    우변 관측 행 수)를 한 쿼리로 잰다.

    support는 'lhs 값 중 rhs가 유일한 비율'(계약 설명) — 그룹 기준이다.
    우변 결측 행은 COUNT(우변)으로 빠진다(위반 아님). 좌변 결측 행은 WHERE로
    뺀다. 별칭은 quoted 식별자와 충돌하지 않는다.
    """
    aliases = [f"r{i}" for i in range(len(rhs_quoted))]
    lhs_group = ", ".join(lhs_quoted)
    lhs_not_null = " AND ".join(f"{column} IS NOT NULL" for column in lhs_quoted)
    aggregates = ", ".join(
        f"COUNT({column}) AS {alias}_rows, COUNT(DISTINCT {column}) AS {alias}_cnt"
        for column, alias in zip(rhs_quoted, aliases, strict=True)
    )
    selects = ", ".join(
        f"COALESCE(SUM(CASE WHEN {alias}_cnt > 1 THEN 1 END), 0), COUNT(*), "
        f"COALESCE(SUM(CASE WHEN {alias}_cnt > 1 THEN {alias}_rows END), 0), "
        f"COALESCE(SUM({alias}_rows), 0)"
        for alias in aliases
    )
    row = ctx.connection.execute(
        f"SELECT {selects} FROM "
        f"(SELECT {lhs_group}, {aggregates} FROM {view} "
        f"WHERE {lhs_not_null} GROUP BY {lhs_group})"
    ).fetchone()
    if row is None:
        return {}
    return {
        column: (
            int(row[i * 4] or 0),
            int(row[i * 4 + 1] or 0),
            int(row[i * 4 + 2] or 0),
            int(row[i * 4 + 3] or 0),
        )
        for i, column in enumerate(rhs_quoted)
    }


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
    """순위(동률 평균)를 매긴 뒤 Pearson. RANK의 동률은 파티션 평균으로 푼다."""
    row = ctx.connection.execute(
        f"WITH pairs AS (SELECT {a} AS x, {b} AS y FROM {view} "
        f"WHERE {a} IS NOT NULL AND {b} IS NOT NULL), "
        f"xr AS (SELECT AVG(rk) OVER (PARTITION BY x) AS rx, y FROM "
        f"(SELECT x, y, RANK() OVER (ORDER BY x) AS rk FROM pairs)), "
        f"yr AS (SELECT rx, AVG(rk) OVER (PARTITION BY y) AS ry FROM "
        f"(SELECT rx, y, RANK() OVER (ORDER BY y) AS rk FROM xr)) "
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


def _drop_key_lhs_transitive(
    fds: list[FunctionalDependency], unique_single: set[str]
) -> list[FunctionalDependency]:
    """키 lhs의 이행(PK→FK→부모 비키)으로 도출되는 FD를 제거한다(리드 #164).

    lhs가 유일 단일 컬럼(키)인 a→c만 대상이다. a→b·b→c가 모두 정확(위반 0)
    성립하면 a→c는 이행으로 도출된다 — 리프 PK가 부모 컬럼 전부를 결정하는
    FD 떼박이를 막는다. 키가 아닌 lhs(예: FK→부모 컬럼)의 직접 종속은 그대로
    둔다(정답 FD 재현율). 근사 경로는 도출 보장이 없어 자격이 없다.
    """
    exact = {(fd.lhs[0], fd.rhs) for fd in fds if len(fd.lhs) == 1 and fd.violation_count == 0}
    middles = {a for a, _ in exact}
    kept: list[FunctionalDependency] = []
    for fd in fds:
        if len(fd.lhs) == 1 and fd.lhs[0] in unique_single:
            a, c = fd.lhs[0], fd.rhs
            # b→c가 성립하고 c→b는 성립하지 않아야 계층상 부모 방향이다
            # (b↔c가 동치면 a→c는 다른 정보라 지우지 않는다).
            if any(
                b != a and b != c and (a, b) in exact and (b, c) in exact and (c, b) not in exact
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
    lhs_budget: int = LHS_BUDGET,
) -> RelationGraph:
    """정규화 뷰 위에서 후보 키·근사 FD·상관을 잰다. 같은 입력은 같은 그래프다."""
    if max_lhs > 3 or max_key_columns > 3:
        raise ValueError("좌변·키 조합은 계약 상한(3컬럼)을 넘을 수 없다")
    view = ctx.normalized_view
    if view is None:
        raise ValueError("S3 정규화 뷰가 없다 — S5는 S3 다음에만 돈다")

    profile_by = {profile.column: profile for profile in profiles}
    type_by = {assignment.column: assignment for assignment in types}
    quoted = {
        column.name: quote_identifier(ctx.column_id_of(column.index)) for column in ingest.columns
    }
    names = [column.name for column in ingest.columns]
    notes: list[str] = []
    # 상수 컬럼은 좌변·우변 모두에서 뺀다(어떤 좌변이든 결정한다 — FD 폭발).
    # 결측이 있는 컬럼은 키·좌변 후보에서 뺀다. 제외 컬럼 이름은 notes에 남긴다.
    constants = sorted(name for name in names if profile_by[name].unique_count <= 1)
    if constants:
        notes.append("상수 컬럼은 좌변·우변 후보에서 뺐다: " + ", ".join(constants))
    missing_cols = sorted(name for name in names if profile_by[name].missing_count > 0)
    if missing_cols:
        notes.append("결측이 있는 컬럼은 키·좌변 후보에서 뺐다: " + ", ".join(missing_cols))
    # 키·좌변 후보: 결측 없는 비상수 컬럼. 우변은 상수가 아닌 컬럼 전부
    # (결측 허용 — 위반 계산에서 뺀다).
    complete = [name for name in names if profile_by[name].missing_count == 0]
    nonconstant = sorted(name for name in complete if profile_by[name].unique_count > 1)
    rhs_pool = sorted(name for name in names if profile_by[name].unique_count > 1)
    row_count = profiles[0].count if profiles else 0
    # 카디널리티는 정규화 뷰에서 직접 잰다(한 컬럼 한 쿼리). S2 프로파일은 원문
    # 기준이라 형식 혼재·시리얼 변형에서 실제보다 크다 — 커트에 쓰면 성립하는
    # FD를 잘못 스킵한다.
    cardinality: dict[str, int] = {}
    for name in names:
        card_row = ctx.connection.execute(
            f"SELECT COUNT(DISTINCT {quoted[name]}) FROM {view}"
        ).fetchone()
        cardinality[name] = int(card_row[0] or 0) if card_row is not None else 0

    # ── 후보 키 ──────────────────────────────────────────────────────
    # 카디널리티 상한 커트(정확한 필요조건): 조합 distinct는 각 컬럼 고유값
    # 수의 곱 이하라, 곱이 행 수에 못 미치면 유일이 불가능하다 — 쿼리하지
    # 않는다.
    keys: list[CandidateKey] = []
    unique_single: set[str] = set()
    key_columns: set[frozenset[str]] = set()
    for size in range(1, max_key_columns + 1):
        for combo in combinations(nonconstant, size):
            combo_set = frozenset(combo)
            # 유일 단일 컬럼을 포함한 복합은 단일로 충분하다(최소성·폭발 제어).
            if size > 1 and unique_single & combo_set:
                continue
            if any(part < combo_set for part in key_columns):
                continue
            combo_cardinality = 1
            for name in combo:
                combo_cardinality *= cardinality[name]
            if combo_cardinality < row_count * 0.98:
                continue
            if _is_unique(ctx, view, [quoted[name] for name in combo]):
                keys.append(CandidateKey(columns=list(combo), unique=True, violation_count=0))
                key_columns.add(combo_set)
                if size == 1:
                    unique_single.add(combo[0])
    keys.sort(key=lambda key: (len(key.columns), key.columns))

    # 동치 키(리드 결정 #164): 유일 단일 컬럼들은 서로 1-1로 결정한다. 대표
    # (컬럼 순서상 첫 번째)만 결정자 역할을 하고, 나머지는 대표와의 상호
    # FD만 남긴다 — 유일 컬럼마다 모든 컬럼을 결정하는 FD가 반복되지 않는다.
    position = {name: index for index, name in enumerate(names)}
    representative = min(unique_single, key=lambda name: position[name]) if unique_single else None

    # ── 근사 함수 종속 ────────────────────────────────────────────────
    # 같은 커트: lhs 조합 카디널리티 상한이 rhs 고유값 수의 97%에 못 미치면
    # 위반 lhs 값이 1%를 넘는다(성립 불가능) — 쿼리하지 않는다. 단일 lhs가
    # 이미 정확히 결정하는 우변도 빼준다(진부분집합 최소성 제거와 같은 결과).
    # 예산은 검증한 좌변 후보 수로 잰다(결정론) — 초과하면 거기까지의 결과와
    # 남은 후보 수를 notes에 남긴다.
    lhs_candidates = [
        lhs
        for size in range(1, max_lhs + 1)
        for lhs in combinations(nonconstant, size)
        if size == 1 or not unique_single & set(lhs)
    ]
    verified = 0
    budget_hit: int | None = None
    fds: list[FunctionalDependency] = []
    exact_single: set[tuple[str, str]] = set()
    for lhs in lhs_candidates:
        if verified >= lhs_budget:
            budget_hit = len(lhs)
            break
        lhs_set = set(lhs)
        size = len(lhs)
        lhs_cardinality = 1
        for name in lhs:
            lhs_cardinality *= cardinality[name]
        targets = [
            name
            for name in rhs_pool
            if name not in lhs_set
            and lhs_cardinality >= cardinality[name] * 0.97
            and (lhs[0], name) not in exact_single
            and (lhs[-1], name) not in exact_single
        ]
        # 비대표 동치 키는 대표에 대한 FD만 낸다(동치 키 규칙). 대표 자신은
        # 모든 우변을 결정자로 검사한다.
        if (
            size == 1
            and representative is not None
            and lhs[0] in unique_single
            and lhs[0] != representative
        ):
            targets = [name for name in targets if name == representative]
        if not targets:
            continue
        verified += 1
        measures = _fd_measures(
            ctx, view, [quoted[name] for name in lhs], [quoted[name] for name in targets]
        )
        for name in targets:
            bad_groups, total_groups, bad_rows, rows_total = measures[quoted[name]]
            if rows_total == 0 or bad_groups > fd_violation_rate * total_groups:
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
            if size == 1 and bad_groups == 0:
                exact_single.add((lhs[0], name))
    if budget_hit is not None:
        remaining = len(lhs_candidates) - verified
        notes.append(
            f"좌변 후보 예산({lhs_budget}) 초과 — 크기 {budget_hit - 1}까지 검증, "
            f"남은 후보 {remaining}개"
        )
    fds = _drop_key_lhs_transitive(_minimal_lhs(fds), unique_single)
    fds.sort(key=lambda fd: (len(fd.lhs), fd.lhs, fd.rhs))
    notes.append("상호정보량·파생 컬럼·계층 구조는 이번 범위 밖이다(빈 목록)")

    # ── 상관 ─────────────────────────────────────────────────────────
    numeric = sorted(
        name
        for name in names
        if type_by[name].physical_type in NUMERIC_TYPES and profile_by[name].unique_count > 1
    )
    categoric = sorted(
        name
        for name in names
        if type_by[name].physical_type in CATEGORY_TYPES
        and 1 < profile_by[name].unique_count <= category_cardinality_limit
    )
    correlations = _correlations(ctx, view, quoted, numeric, categoric)

    return RelationGraph(
        candidate_keys=keys,
        functional_dependencies=fds,
        correlations=correlations,
        notes=notes,
    )
