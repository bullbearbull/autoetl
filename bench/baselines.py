"""기준선 예측 3종 — 데이터를 읽지 않고 만들 수 있는 최소 예측.

헤더에서 정답 테이블 정보를 제거한 뒤(T-0015) '헤더만 보고 100점'이 나오지
않는지를 확인하는 판정 기준점이다. bench/README.md의 기준선 표와 문서↔코드
일치 테스트(test_bench_scoring)가 이 값들을 대조한다.

세 예측 모두 타입은 전부 STRING으로 찍고 FK·FD는 예측하지 않는다. 이름 규칙과
위치 끊기는 헤더 이름에 든 실제 신호를 어디까지 활용할 수 있는지를 보여준다.
hard 변형은 헤더가 col_01 형식(이름 신호 없음)에 컬럼 순서까지 시드 고정으로
섞어 위치 신호도 없다(T-0037) — 셋 다 단일 와이드와 같아진다.
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


def position_break_baseline(answer: BenchAnswer, variant: str) -> BenchPrediction:
    """'_id'로 끝나는 컬럼 위치마다 테이블을 끊는 예측(첫 컬럼을 PK로).

    헤더 이름 신호만 썼을 때 실제로 얻을 수 있는 점수를 기록하는 기준점이다
    (소유자 재리뷰 — '_id' 위치 끊기가 이름 규칙 기준선보다 높다). hard 변형은
    헤더가 col_01 형식이라 끊을 이름이 없어 단일 와이드와 같아진다.
    """
    header = answer.variant_headers[variant]
    starts = [i for i, name in enumerate(header) if name.endswith("_id")]
    if not starts:
        return wide_table_baseline(answer, variant)
    segments: list[list[str]] = []
    if starts[0] > 0:
        segments.append(header[: starts[0]])
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(header)
        segments.append(header[start:end])
    return BenchPrediction(
        tables=[
            PredictedTable(
                columns=[
                    PredictedColumn(name=name, physical_type=PhysicalType.STRING)
                    for name in segment
                ],
                primary_key=[segment[0]],
            )
            for segment in segments
        ]
    )
