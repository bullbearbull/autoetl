"""벤치 정답 형식·채점 지표·판정 기준 검증."""

from __future__ import annotations

import pytest
from bench.answer import (
    AnswerColumn,
    AnswerForeignKey,
    AnswerFunctionalDependency,
    AnswerTable,
    BenchAnswer,
    BenchPrediction,
    PredictedColumn,
    PredictedTable,
)
from bench.criteria import (
    MINIMUM_MODEL_RATIO,
    REQUIRED_JSON_COMPLIANCE,
    GateStatus,
    RunMetrics,
    check_gates,
    classify_models,
)
from bench.scoring import (
    adjusted_rand_index,
    foreign_key_f1,
    functional_dependency_recall,
    primary_key_f1,
    schema_recovery_score,
    table_count_error,
    type_f1,
)
from pydantic import ValidationError

from autoetl.contracts import PhysicalType


def demo_answer() -> BenchAnswer:
    return BenchAnswer(
        pack="demo",
        tables=[
            AnswerTable(
                name="a",
                primary_key=["a_id"],
                columns=[
                    AnswerColumn(name="a_id", physical_type=PhysicalType.STRING),
                    AnswerColumn(name="a_name", physical_type=PhysicalType.STRING),
                    AnswerColumn(name="a_amt", physical_type=PhysicalType.DECIMAL),
                ],
                functional_dependencies=[
                    AnswerFunctionalDependency(table="a", determinant=["a_id"], dependent="a_name"),
                    AnswerFunctionalDependency(table="a", determinant=["a_id"], dependent="a_amt"),
                ],
                row_count=10,
            ),
            AnswerTable(
                name="b",
                primary_key=["b_id"],
                columns=[
                    AnswerColumn(name="b_id", physical_type=PhysicalType.STRING),
                    AnswerColumn(
                        name="b_a_id",
                        physical_type=PhysicalType.STRING,
                        is_foreign_key=True,
                    ),
                ],
                foreign_keys=[
                    AnswerForeignKey(
                        table="b",
                        columns=["b_a_id"],
                        references_table="a",
                        references_columns=["a_id"],
                    )
                ],
                functional_dependencies=[
                    AnswerFunctionalDependency(table="b", determinant=["b_id"], dependent="b_a_id")
                ],
                row_count=50,
            ),
        ],
    )


def perfect_prediction(answer: BenchAnswer) -> BenchPrediction:
    tables: list[PredictedTable] = []
    for table in answer.tables:
        tables.append(
            PredictedTable(
                columns=[
                    PredictedColumn(
                        name=f"{table.name}_{column.name}",
                        physical_type=column.physical_type,
                        is_foreign_key=column.is_foreign_key,
                    )
                    for column in table.columns
                ],
                primary_key=[f"{table.name}_{pk}" for pk in table.primary_key],
                foreign_keys=[
                    AnswerForeignKey(
                        table="",
                        columns=[f"{fk.table}_{c}" for c in fk.columns],
                        references_table="",
                        references_columns=[
                            f"{fk.references_table}_{c}" for c in fk.references_columns
                        ],
                    )
                    for fk in table.foreign_keys
                ],
                functional_dependencies=[
                    AnswerFunctionalDependency(
                        table="",
                        determinant=[f"{table.name}_{c}" for c in fd.determinant],
                        dependent=f"{table.name}_{fd.dependent}",
                    )
                    for fd in table.functional_dependencies
                ],
            )
        )
    return BenchPrediction(tables=tables)


def test_perfect_prediction_scores_full() -> None:
    answer = demo_answer()
    prediction = perfect_prediction(answer)
    assert type_f1(answer, prediction) == pytest.approx(1.0)
    assert primary_key_f1(answer, prediction) == pytest.approx(1.0)
    assert foreign_key_f1(answer, prediction) == pytest.approx(1.0)
    assert functional_dependency_recall(answer, prediction) == pytest.approx(1.0)
    assert adjusted_rand_index(answer, prediction) == pytest.approx(1.0)
    assert table_count_error(answer, prediction) == 0
    assert schema_recovery_score(answer, prediction) == pytest.approx(100.0)


def test_one_wrong_type_drops_type_f1() -> None:
    answer = demo_answer()
    prediction = perfect_prediction(answer)
    # 5개 컬럼 중 1개(a.a_amt → 평면 이름 a_a_amt) 타입을 틀리게 예측
    for table in prediction.tables:
        for column in table.columns:
            if column.name == "a_a_amt":
                column.physical_type = PhysicalType.INTEGER
    # 정밀도 1.0(모두 아는 컬럼), 재현율 4/5 → F1 = 2*1*0.8/1.8
    assert type_f1(answer, prediction) == pytest.approx(2 * 0.8 / 1.8)


def test_missing_table_lowers_ari_and_pk() -> None:
    answer = demo_answer()
    # 모든 컬럼을 한 테이블로 몰아 넣은(정규화 실패) 예측
    flat = perfect_prediction(answer)
    merged_columns = [column for table in flat.tables for column in table.columns]
    merged = BenchPrediction(
        tables=[
            PredictedTable(
                columns=merged_columns,
                primary_key=["a_a_id"],
                foreign_keys=[
                    AnswerForeignKey(
                        table="",
                        columns=["b_b_a_id"],
                        references_table="",
                        references_columns=["a_a_id"],
                    )
                ],
                functional_dependencies=[],
            )
        ]
    )
    # 예측이 한 덩어리면 ARI = 0 (b_comb = c2(n)이 되어 분자가 0으로 수렴)
    assert adjusted_rand_index(answer, merged) == pytest.approx(0.0)
    # PK: 정담 2개 중 1개 재현, 예측 1개 → 정밀도 1, 재현율 .5
    assert primary_key_f1(answer, merged) == pytest.approx(2 * 0.5 / 1.5)
    assert table_count_error(answer, merged) == -1
    assert functional_dependency_recall(answer, merged) == 0.0
    assert schema_recovery_score(answer, merged) < 60.0


def test_dropped_fk_zeroes_fk_f1() -> None:
    answer = demo_answer()
    prediction = perfect_prediction(answer)
    prediction.tables[1].foreign_keys = []
    assert foreign_key_f1(answer, prediction) == 0.0


def test_answer_format_is_closed() -> None:
    answer = demo_answer()
    payload = answer.model_dump(mode="json")
    payload["tables"][0]["bogus"] = 1
    with pytest.raises(ValidationError):
        BenchAnswer.model_validate(payload)


# ── 관문·판정 ────────────────────────────────────────────────────────────────


def run(
    model: str,
    params: float,
    score: float,
    *,
    ddl: float = 1.0,
    leak: int = 0,
    json_ok: float = 1.0,
    cost: float = 1.0,
) -> RunMetrics:
    return RunMetrics(
        model=model,
        parameters_b=params,
        score=score,
        ddl_success_rate=ddl,
        leak_count=leak,
        json_compliance=json_ok,
        relative_cost=cost,
    )


def test_gates() -> None:
    ok = check_gates(run("m", 8, 90))
    assert ok.passed and ok.ddl is GateStatus.PASSED
    assert not check_gates(run("m", 8, 90, ddl=0.98)).passed
    assert not check_gates(run("m", 8, 90, leak=1)).passed
    assert not check_gates(run("m", 8, 90, json_ok=REQUIRED_JSON_COMPLIANCE - 0.01)).passed


def test_classify_models_minimum_and_recommended() -> None:
    runs = [
        run("big", 70, 90, cost=10.0),
        run("mid", 8, 78, cost=2.0),
        run("small", 4, 70, cost=1.0),
    ]
    result = classify_models(runs)
    # 최소: 90*0.85=76.5 이상(big, mid) 중 가장 작은 mid
    assert result.minimum_model == "mid"
    # 권장: 90*0.95=85.5 이상은 big뿐
    assert result.recommended_model == "big"
    assert result.best_score == 90.0
    assert MINIMUM_MODEL_RATIO == 0.85


def test_classify_models_gate_failure_excludes() -> None:
    runs = [
        run("leaky", 70, 99, leak=1),
        run("clean", 8, 80),
    ]
    result = classify_models(runs)
    assert result.minimum_model == "clean"
    assert result.recommended_model == "clean"


def test_classify_models_no_qualifier() -> None:
    result = classify_models([run("leaky", 8, 50, leak=2)])
    assert result.minimum_model is None
    assert result.recommended_model is None
    assert "관문" in result.reason
