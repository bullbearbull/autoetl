"""S1 샘플링 — SamplingPlan(T-0024).

행 수가 전수 한도 이하면 전수로 검사한다(기본 1,000,000행, 인자로 설정 가능).
한도를 넘면 시드를 고정한 무작위 표본을 쓴다:

- 표본은 DuckDB `USING SAMPLE n ROWS (reservoir, 시드)`로 만든다. 같은 시드는
  같은 표본을 낸다(정렬 부 subquery를 깔아 스레드 수와 무관하게 스캔 순서를
  고정한다 — 검증 테스트가 대조한다).
- 표본 테이블 이름을 `ctx.stage_artifacts["s01_sampling.sample_relation"]`에
  남긴다. S2는 전수가 필요한 집계(결측 수·고유값 수)를 DuckDB로 전수 계산하고
  길이·형태 통계만 표본에서 뽑는다.
- 층화·시간 균등 표본은 이번에는 하지 않는다: 기준이 될 컬럼 타입이 S3에서
  정해지기 전이라 층·시간 축을 확정할 수 없다. rationale에 이 사유를 남긴다.
"""

from __future__ import annotations

from autoetl.contracts import SamplingMethod, SamplingPlan
from autoetl.core.context import RAW_TABLE, AnalysisContext, quote_identifier

FULL_SCAN_ROW_LIMIT = 1_000_000
"""전수 한도(행). 이하면 전수로 검사한다. 인자로 바꿀 수 있다."""

SAMPLE_ROW_LIMIT = 100_000
"""표본 크기 기본값(행). 실제 크기는 전체 행 수 이하로 줄인다."""

DEFAULT_SEED = 42
"""표본 재현용 기본 시드."""

SAMPLE_RELATION = "s1_sample"
"""표본 테이블 이름. 내부 식별자 규칙([a-z][a-z0-9_]*)을 지킨다."""

SAMPLE_RELATION_KEY = "s01_sampling.sample_relation"
"""표본 테이블 이름을 올리는 stage_artifacts 키."""


def run(
    ctx: AnalysisContext,
    *,
    full_scan_limit: int = FULL_SCAN_ROW_LIMIT,
    sample_limit: int = SAMPLE_ROW_LIMIT,
    seed: int = DEFAULT_SEED,
) -> SamplingPlan:
    """행 수에 따라 전수/표본을 정하고, 표본이면 테이블을 만들어 둔다."""
    total_rows = ctx.row_count
    if total_rows <= full_scan_limit:
        return SamplingPlan(
            strategy="full",
            total_rows=total_rows,
            rationale=(
                f"행 수 {total_rows}이(가) 전수 한도 {full_scan_limit} 이하라 전수로 검사한다"
            ),
        )

    sample_rows = min(sample_limit, total_rows)
    # 정렬 부 subquery: 스캔 순서를 고정해 시드 재현성이 스레드 수와 무관하게 한다.
    # 식별자·리터럴은 전부 코드가 만든 값이다(파일에서 온 문자열 없음).
    ctx.connection.execute(
        f"CREATE OR REPLACE TABLE {SAMPLE_RELATION} AS "
        f"SELECT * FROM (SELECT * FROM {RAW_TABLE} ORDER BY "
        f"{', '.join(quote_identifier(column.column_id) for column in ctx.columns)}) "
        f"USING SAMPLE {sample_rows} ROWS (reservoir, {seed})"
    )
    ctx.stage_artifacts[SAMPLE_RELATION_KEY] = SAMPLE_RELATION
    return SamplingPlan(
        strategy="sample",
        method=SamplingMethod.RANDOM,
        total_rows=total_rows,
        sample_rows=sample_rows,
        seed=seed,
        rationale=(
            f"행 수 {total_rows}이(가) 전수 한도 {full_scan_limit}을 넘어 시드 고정 "
            f"무작위 표본 {sample_rows}행으로 검사한다. 층화·시간 균등 표본은 기준이 "
            "될 컬럼 타입이 S3에서 정해지기 전이라 이번에는 하지 않는다"
        ),
    )
