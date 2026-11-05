"""채점 지표 계산 — 순수 함수.

docs/PLAN.md 벤치 지표 표의 스키마 복원 영역을 다룬다: 타입 F1, PK·FK 정밀도·
재현율, FD 복원율, 컬럼-테이블 배정 일치도(ARI), 테이블 수 오차. DDL·적재·안정성·
보안·비용 지표는 실행기(런너)가 실측값을 채운다.

채점 키 규칙(v0 합의): 예측(채점용)의 컬럼 이름은 평면 입력 파일의 헤더 이름,
즉 정답의 '테이블_컬럼'을 그대로 보존한다. 예측 테이블 이름은 채점에 쓰지 않는다.
"""

from __future__ import annotations

from bench.answer import BenchAnswer, BenchPrediction


def _flat(table: str, column: str) -> str:
    """정답의 (테이블, 컬럼)을 평면 헤더 이름으로 바꾼다."""
    return f"{table}_{column}"


def _truth_columns(answer: BenchAnswer) -> dict[str, str]:
    """평면 컬럼 이름 → 물리 타입."""
    return {
        _flat(table.name, column.name): column.physical_type
        for table in answer.tables
        for column in table.columns
    }


def _predicted_columns(prediction: BenchPrediction) -> dict[str, str]:
    return {
        column.name: column.physical_type for table in prediction.tables for column in table.columns
    }


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def type_f1(answer: BenchAnswer, prediction: BenchPrediction) -> float:
    """컬럼 단위 물리 타입 일치 F1.

    정밀도·재현율 모두 '타입까지 맞은 컬럼'만 센다. 이름만 알고 타입을 틀린
    컬럼은 정밀도를 깎는다.
    """
    truth = _truth_columns(answer)
    predicted = _predicted_columns(prediction)
    if not truth:
        return 1.0
    matches = sum(1 for key, value in truth.items() if predicted.get(key) == value)
    recall = matches / len(truth)
    correct = sum(1 for key, value in predicted.items() if truth.get(key) == value)
    precision = correct / len(predicted) if predicted else 0.0
    return _f1(precision, recall)


def _truth_pk_sets(answer: BenchAnswer) -> list[frozenset[str]]:
    return [
        frozenset(_flat(table.name, column) for column in table.primary_key)
        for table in answer.tables
    ]


def primary_key_f1(answer: BenchAnswer, prediction: BenchPrediction) -> float:
    """PK 집합 일치 F1. 컬럼 집합이 같으면 같은 PK로 본다."""
    truth = _truth_pk_sets(answer)
    predicted = [frozenset(table.primary_key) for table in prediction.tables if table.primary_key]
    if not truth:
        return 1.0
    truth_set, predicted_set = set(truth), set(predicted)
    recall = len(truth_set & predicted_set) / len(truth_set)
    precision = len(truth_set & predicted_set) / len(predicted_set) if predicted_set else 0.0
    return _f1(precision, recall)


def _truth_fk_pairs(answer: BenchAnswer) -> set[tuple[frozenset[str], frozenset[str]]]:
    return {
        (
            frozenset(_flat(fk.table, column) for column in fk.columns),
            frozenset(_flat(fk.references_table, column) for column in fk.references_columns),
        )
        for table in answer.tables
        for fk in table.foreign_keys
    }


def foreign_key_f1(answer: BenchAnswer, prediction: BenchPrediction) -> float:
    """FK (자식 컬럼 집합, 부모 컬럼 집합) 일치 F1."""
    truth = _truth_fk_pairs(answer)
    predicted = {
        (frozenset(fk.columns), frozenset(fk.references_columns))
        for table in prediction.tables
        for fk in table.foreign_keys
    }
    if not truth:
        return 1.0
    hits = len(truth & predicted)
    recall = hits / len(truth)
    precision = hits / len(predicted) if predicted else 0.0
    return _f1(precision, recall)


def functional_dependency_recall(answer: BenchAnswer, prediction: BenchPrediction) -> float:
    """FD 복원율(PLAN 지표): 정답 FD 중 예측이 찾은 비율. 식별은 (결정자 집합, 피결정자)."""
    truth = _truth_fds(answer)
    predicted = _predicted_fds(prediction)
    if not truth:
        return 1.0
    return len(truth & predicted) / len(truth)


def functional_dependency_f1(answer: BenchAnswer, prediction: BenchPrediction) -> float:
    """FD F1 — 재현율만 쓰면 모든 컬럼 쌍을 FD로 나열한 예측도 100%가 된다.

    종합 점수에는 이 F1을 넣어 정밀도를 함께 잰다.
    """
    truth = _truth_fds(answer)
    predicted = _predicted_fds(prediction)
    if not truth:
        return 1.0
    hits = len(truth & predicted)
    recall = hits / len(truth)
    precision = hits / len(predicted) if predicted else 0.0
    return _f1(precision, recall)


def _truth_fds(answer: BenchAnswer) -> set[tuple[frozenset[str], str]]:
    return {
        (
            frozenset(_flat(fd.table, column) for column in fd.determinant),
            _flat(fd.table, fd.dependent),
        )
        for table in answer.tables
        for fd in table.functional_dependencies
    }


def _predicted_fds(prediction: BenchPrediction) -> set[tuple[frozenset[str], str]]:
    return {
        (frozenset(fd.determinant), fd.dependent)
        for table in prediction.tables
        for fd in table.functional_dependencies
    }


def adjusted_rand_index(answer: BenchAnswer, prediction: BenchPrediction) -> float:
    """컬럼-테이블 배정 일치도(ARI). 1.0 완전 일치, 0 무작위 수준, 음수 가능.

    정답 배정은 평면 이름 → 정답 테이블, 예측 배정은 평면 이름 → 예측 테이블
    인덱스로 두고 표준 ARI 식으로 계산한다.
    """
    truth_assignment = {
        _flat(table.name, column.name): table.name
        for table in answer.tables
        for column in table.columns
    }
    predicted_assignment: dict[str, int] = {}
    for i, table in enumerate(prediction.tables):
        for column in table.columns:
            predicted_assignment[column.name] = i
    common = [key for key in truth_assignment if key in predicted_assignment]
    if not common:
        return 0.0
    n = len(common)
    if n < 2:
        # 겹치는 컬럼이 1개면 배정 비교 정보가 없다(자명하게 일치로 본다).
        return 1.0
    contingency: dict[tuple[str, int], int] = {}
    truth_totals: dict[str, int] = {}
    predicted_totals: dict[int, int] = {}
    for key in common:
        pair = (truth_assignment[key], predicted_assignment[key])
        contingency[pair] = contingency.get(pair, 0) + 1
        truth_totals[pair[0]] = truth_totals.get(pair[0], 0) + 1
        predicted_totals[pair[1]] = predicted_totals.get(pair[1], 0) + 1

    def c2(x: int) -> int:
        return x * (x - 1) // 2

    sum_comb = sum(c2(count) for count in contingency.values())
    a_comb = sum(c2(total) for total in truth_totals.values())
    b_comb = sum(c2(total) for total in predicted_totals.values())
    expected = a_comb * b_comb / c2(n)
    maximum = (a_comb + b_comb) / 2
    if maximum == expected:
        return 1.0
    return (sum_comb - expected) / (maximum - expected)


def table_count_error(answer: BenchAnswer, prediction: BenchPrediction) -> int:
    """테이블 수 오차(예측 - 정답). 0이 최선."""
    return len(prediction.tables) - len(answer.tables)


def schema_recovery_score(answer: BenchAnswer, prediction: BenchPrediction) -> float:
    """스키마 복원 종합 점수(0~100).

    다섯 지표(타입 F1·PK F1·FK F1·FD F1·ARI)의 균등 가중 평균이다.
    FD는 PLAN 지표(복원율) 외에 스팸 예측 방지를 위해 F1을 쓴다.
    가중치 변경은 소유자 검토 대상이다(후속 T-0012 A/B 검증).
    """
    metrics = (
        type_f1(answer, prediction),
        primary_key_f1(answer, prediction),
        foreign_key_f1(answer, prediction),
        functional_dependency_f1(answer, prediction),
        max(adjusted_rand_index(answer, prediction), 0.0),
    )
    return 100.0 * sum(metrics) / len(metrics)
