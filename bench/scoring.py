"""채점 지표 계산 — 순수 함수.

docs/PLAN.md 벤치 지표 표의 스키마 복원 영역을 다룬다: 타입 F1, PK·FK 정밀도·
재현율, FD 복원율, 컬럼-테이블 배정 일치도(ARI), 테이블 수 오차. DDL·적재·안정성·
보안·비용 지표는 실행기(런너)가 실측값을 채운다.

채점 키 규칙(T-0015): 정답 컬럼은 컬럼 ID(테이블 접두사가 없는 이름, 조인 키는
부모 PK 이름 하나)로 식별한다. 예측의 컬럼 이름은 채점 대상 변형 CSV의 헤더
문자열이고, 정답의 variant_headers와 위치가 같으므로 헤더 이름 → 컬럼 ID 대응으로
바꿔 채점한다. 예측 테이블 이름은 채점에 쓰지 않는다. FK 식별은 (자식 테이블 PK의
컬럼 ID 집합, FK 컬럼 ID 집합) 쌍 — 조인 키가 부모·자식에서 같은 평면 컬럼이라
예전의 (자식 컬럼, 부모 컬럼) 쌍으로는 구분되지 않기 때문이다.
"""

from __future__ import annotations

from collections.abc import Sized

from autoetl.contracts.common import PhysicalType
from bench.answer import BenchAnswer, BenchPrediction


def _column_ids(answer: BenchAnswer) -> list[str]:
    """정답의 평면 컬럼 ID(테이블 선언 순, 조인 키는 첫 등장 한 번)."""
    ids: list[str] = []
    seen: set[str] = set()
    for table in answer.tables:
        for column in table.columns:
            if column.column_id not in seen:
                seen.add(column.column_id)
                ids.append(column.column_id)
    return ids


def _id_map(answer: BenchAnswer, variant: str) -> dict[str, str]:
    """변형 헤더 이름 → 컬럼 ID. 변형 헤더와 정답 컬럼은 위치가 같다."""
    headers = answer.variant_headers.get(variant)
    if headers is None:
        raise ValueError(
            f"정답에 변형 '{variant}'의 헤더가 없다(variant_headers 키: "
            f"{sorted(answer.variant_headers)})"
        )
    ids = _column_ids(answer)
    if len(headers) != len(ids):
        raise ValueError(
            f"변형 '{variant}' 헤더 길이({len(headers)})가 정답 컬럼 수({len(ids)})와 다르다"
        )
    return dict(zip(headers, ids, strict=True))


def _mapped(name: str, id_map: dict[str, str]) -> str:
    """예측 컬럼 이름을 컬럼 ID로 바꾼다. 모르는 이름은 그대로 둔다(일치 불가)."""
    return id_map.get(name, name)


def _truth_name_to_id(answer: BenchAnswer) -> dict[str, dict[str, str]]:
    """테이블 이름 → (원본 컬럼 이름 → 컬럼 ID)."""
    return {
        table.name: {column.name: column.column_id for column in table.columns}
        for table in answer.tables
    }


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _perfect_if_empty(truth: Sized, predicted: Sized) -> bool | None:
    """정답·예측이 모두 비어 있으면 만점, 정답만 비었으면 0점.

    정답이 비었는데 예측을 늘어놓으면 관련 지표가 1.0이 되는 스팸 관용을 막는다.
    반환 None은 정답이 비어 있지 않아 usual 계산으로 넘어가야 한다는 뜻이다.
    """
    if not truth:
        return not predicted
    return None


def type_f1(answer: BenchAnswer, prediction: BenchPrediction, variant: str) -> float:
    """컬럼 단위 물리 타입 일치 F1. 컬럼 ID 하나당 한 번만 센다.

    정밀도·재현율 모두 '타입까지 맞은 컬럼'만 센다. 조인 키를 부모·자식 두 테이블에
    모두 예측해도 평면 컬럼은 하나이므로 한 번만 집계된다.
    """
    truth: dict[str, PhysicalType] = {}
    for table in answer.tables:
        for column in table.columns:
            truth.setdefault(column.column_id, column.physical_type)
    predicted: dict[str, PhysicalType] = {}
    for predicted_table in prediction.tables:
        for predicted_column in predicted_table.columns:
            predicted.setdefault(
                _mapped(predicted_column.name, _id_map(answer, variant)),
                predicted_column.physical_type,
            )
    empty = _perfect_if_empty(truth, predicted)
    if empty is not None:
        return 1.0 if empty else 0.0
    matches = sum(1 for key, value in truth.items() if predicted.get(key) == value)
    recall = matches / len(truth)
    correct = sum(1 for key, value in predicted.items() if truth.get(key) == value)
    precision = correct / len(predicted) if predicted else 0.0
    return _f1(precision, recall)


def _truth_pk_sets(answer: BenchAnswer) -> list[frozenset[str]]:
    return [
        frozenset(_truth_name_to_id(answer)[table.name][name] for name in table.primary_key)
        for table in answer.tables
    ]


def primary_key_f1(answer: BenchAnswer, prediction: BenchPrediction, variant: str) -> float:
    """PK 집합 일치 F1. 컬럼 ID 집합이 같으면 같은 PK로 본다.

    정밀도 분모는 예측 개수다(중복 포함). 같은 PK를 여러 테이블에 걸쳐 예측하면
    분자는 한 번만 인정되므로 정밀도가 떨어진다(T-0016).
    """
    truth = _truth_pk_sets(answer)
    id_map = _id_map(answer, variant)
    predicted = [
        frozenset(_mapped(name, id_map) for name in predicted_table.primary_key)
        for predicted_table in prediction.tables
        if predicted_table.primary_key
    ]
    empty = _perfect_if_empty(truth, predicted)
    if empty is not None:
        return 1.0 if empty else 0.0
    truth_set = set(truth)
    hits = sum(1 for key in truth_set if key in predicted)
    recall = hits / len(truth_set)
    precision = hits / len(predicted) if predicted else 0.0
    return _f1(precision, recall)


def _truth_fk_pairs(answer: BenchAnswer) -> set[tuple[frozenset[str], frozenset[str]]]:
    """FK = (자식 테이블 PK의 컬럼 ID 집합, FK 컬럼 ID 집합)."""
    name_to_id = _truth_name_to_id(answer)
    pairs: set[tuple[frozenset[str], frozenset[str]]] = set()
    for table in answer.tables:
        pk_ids = frozenset(name_to_id[table.name][name] for name in table.primary_key)
        for fk in table.foreign_keys:
            pairs.add(
                (
                    pk_ids,
                    frozenset(name_to_id[table.name][name] for name in fk.columns),
                )
            )
    return pairs


def foreign_key_f1(answer: BenchAnswer, prediction: BenchPrediction, variant: str) -> float:
    """FK (자식 PK 컬럼 ID 집합, FK 컬럼 ID 집합) 쌍 일치 F1.

    정밀도 분모는 예측 개수다(중복 포함) — 같은 FK를 여러 번 예측해도 분자는
    한 번만 인정된다(T-0016).
    """
    truth = _truth_fk_pairs(answer)
    id_map = _id_map(answer, variant)
    predicted: list[tuple[frozenset[str], frozenset[str]]] = []
    for predicted_table in prediction.tables:
        pk_ids = frozenset(_mapped(name, id_map) for name in predicted_table.primary_key)
        for fk in predicted_table.foreign_keys:
            predicted.append((pk_ids, frozenset(_mapped(name, id_map) for name in fk.columns)))
    empty = _perfect_if_empty(truth, predicted)
    if empty is not None:
        return 1.0 if empty else 0.0
    hits = sum(1 for pair in truth if pair in predicted)
    recall = hits / len(truth)
    precision = hits / len(predicted) if predicted else 0.0
    return _f1(precision, recall)


def _truth_fds(answer: BenchAnswer) -> set[tuple[frozenset[str], str]]:
    name_to_id = _truth_name_to_id(answer)
    return {
        (
            frozenset(name_to_id[fd.table][name] for name in fd.determinant),
            name_to_id[fd.table][fd.dependent],
        )
        for table in answer.tables
        for fd in table.functional_dependencies
    }


def _predicted_fds(
    prediction: BenchPrediction, id_map: dict[str, str]
) -> list[tuple[frozenset[str], str]]:
    """예측 FD 목록. 중복을 그대로 세기 위해 list로 돌려준다(T-0016)."""
    return [
        (frozenset(_mapped(name, id_map) for name in fd.determinant), _mapped(fd.dependent, id_map))
        for predicted_table in prediction.tables
        for fd in predicted_table.functional_dependencies
    ]


def functional_dependency_recall(
    answer: BenchAnswer, prediction: BenchPrediction, variant: str
) -> float:
    """FD 복원율(PLAN 지표): 정답 FD 중 예측이 찾은 비율. 식별은 (결정자 집합, 피결정자)."""
    truth = _truth_fds(answer)
    predicted = _predicted_fds(prediction, _id_map(answer, variant))
    empty = _perfect_if_empty(truth, predicted)
    if empty is not None:
        return 1.0 if empty else 0.0
    return sum(1 for fd in truth if fd in predicted) / len(truth)


def functional_dependency_f1(
    answer: BenchAnswer, prediction: BenchPrediction, variant: str
) -> float:
    """FD F1 — 재현율만 쓰면 모든 컬럼 쌍을 FD로 나열한 예측도 100%가 된다.

    종합 점수에는 이 F1을 넣어 정밀도를 함께 잰다. 정밀도 분모는 예측 개수다
    (중복 포함) — 같은 FD를 여러 번 예측해도 분자는 한 번만 인정된다(T-0016).
    """
    truth = _truth_fds(answer)
    predicted = _predicted_fds(prediction, _id_map(answer, variant))
    empty = _perfect_if_empty(truth, predicted)
    if empty is not None:
        return 1.0 if empty else 0.0
    hits = sum(1 for fd in truth if fd in predicted)
    recall = hits / len(truth)
    precision = hits / len(predicted) if predicted else 0.0
    return _f1(precision, recall)


def adjusted_rand_index(answer: BenchAnswer, prediction: BenchPrediction, variant: str) -> float:
    """컬럼-테이블 배정 일치도(ARI). 1.0 완전 일치, 0 무작위 수준, 음수 가능.

    조인 키는 그것이 PK인 테이블 소속으로 본다(예측에서도 PK로 쓰인 테이블
    소속, PK로 쓰인 곳이 없으면 처음 나온 테이블 소속).

    최종값은 겹침 비율(겹친 컬럼 수 / 정답 컬럼 수)을 곱한다. 겹친 컬럼만으로
    ARI를 재면 컬럼 1개만 예측해도 만점이 나와, 예측을 늘릴수록 점수가 내려가는
    구간이 생긴다. 정답에 없는 예측 전용 컬럼(환각)은 여기서 벌점하지 않는다
    — 타입·PK 정밀도가 이미 깎는다.
    """
    truth_assignment: dict[str, str] = {}
    name_to_id = _truth_name_to_id(answer)
    for table in answer.tables:
        for column in table.columns:
            truth_assignment.setdefault(column.column_id, table.name)
    for table in answer.tables:
        for pk_name in table.primary_key:
            truth_assignment[name_to_id[table.name][pk_name]] = table.name

    id_map = _id_map(answer, variant)
    predicted_assignment: dict[str, int] = {}
    for i, predicted_table in enumerate(prediction.tables):
        for predicted_column in predicted_table.columns:
            predicted_assignment.setdefault(_mapped(predicted_column.name, id_map), i)
    for i, predicted_table in enumerate(prediction.tables):
        for pk_name in predicted_table.primary_key:
            predicted_assignment[_mapped(pk_name, id_map)] = i

    common = [key for key in truth_assignment if key in predicted_assignment]
    if not common:
        return 0.0
    n = len(common)
    coverage = n / len(truth_assignment)
    if n < 2:
        # 겹치는 컬럼이 1개면 배정 비교 정보가 없어 자명 일치(1.0)로 두되,
        # 커버리지 곱으로 예측을 안 한 나머지 컬럼만큼 깎인다.
        return coverage
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
        return coverage
    return (sum_comb - expected) / (maximum - expected) * coverage


def table_count_error(answer: BenchAnswer, prediction: BenchPrediction) -> int:
    """테이블 수 오차(예측 - 정답). 0이 최선."""
    return len(prediction.tables) - len(answer.tables)


def schema_recovery_score(answer: BenchAnswer, prediction: BenchPrediction, variant: str) -> float:
    """스키마 복원 종합 점수(0~100).

    다섯 지표(타입 F1·PK F1·FK F1·FD F1·ARI)의 균등 가중 평균이다.
    FD는 PLAN 지표(복원율) 외에 스팸 예측 방지를 위해 F1을 쓴다.
    가중치 변경은 소유자 검토 대상이다(후속 T-0012 A/B 검증).
    """
    metrics = (
        type_f1(answer, prediction, variant),
        primary_key_f1(answer, prediction, variant),
        foreign_key_f1(answer, prediction, variant),
        functional_dependency_f1(answer, prediction, variant),
        max(adjusted_rand_index(answer, prediction, variant), 0.0),
    )
    return 100.0 * sum(metrics) / len(metrics)
