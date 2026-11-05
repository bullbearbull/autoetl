"""기준선 예측 2종 — 데이터를 읽지 않고 만들 수 있는 최소 예측.

헤더에서 정답 테이블 정보를 제거한 뒤(T-0015) '헤더만 보고 100점'이 나오지
않는지를 확인하는 판정 기준점이다. bench/README.md의 기준선 표와 문서↔코드
일치 테스트(test_bench_scoring)가 이 값들을 대조한다.

두 예측 모두 타입은 전부 STRING으로 찍고 FK·FD는 예측하지 않는다. hard 변형은
헤더가 col_01 형식이라 '_id' 규칙이 묵살되어 이름 규칙 예측이 단일 와이드와
같아진다 — 그래서 hard 기준선은 와이드 하나로 충분하다는 점도 함께 보여준다.
"""

from __future__ import annotations

from autoetl.contracts.common import PhysicalType
from bench.answer import BenchAnswer, BenchPrediction, PredictedColumn, PredictedTable


def wide_table_baseline(answer: BenchAnswer, variant: str) -> BenchPrediction:
    """모든 컬럼을 STRING 단일 와이드 테이블로 예측한다(정규화 0 시도)."""
    header = answer.variant_headers[variant]
    return BenchPrediction(
        tables=[
            PredictedTable(
                columns=[
                    PredictedColumn(name=name, physical_type=PhysicalType.STRING) for name in header
                ],
                primary_key=[header[0]],
            )
        ]
    )


def name_rule_baseline(answer: BenchAnswer, variant: str) -> BenchPrediction:
    """헤더 이름 규칙만 쓴 예측: '_id'로 끝나는 컬럼은 각각 PK 단독 테이블,
    나머지는 한 와이드 테이블."""
    header = answer.variant_headers[variant]
    key_columns = [name for name in header if name.endswith("_id")]
    other_columns = [name for name in header if not name.endswith("_id")]
    tables = [
        PredictedTable(
            columns=[PredictedColumn(name=name, physical_type=PhysicalType.STRING)],
            primary_key=[name],
        )
        for name in key_columns
    ]
    if other_columns:
        tables.append(
            PredictedTable(
                columns=[
                    PredictedColumn(name=name, physical_type=PhysicalType.STRING)
                    for name in other_columns
                ],
                primary_key=[other_columns[0]],
            )
        )
    return BenchPrediction(tables=tables)
