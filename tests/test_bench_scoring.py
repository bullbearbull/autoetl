"""벤치 정답 형식·채점 지표·판정 기준 검증(T-0015 컬럼 ID 키 기준)."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
from bench.answer import (
    ANSWER_VERSION,
    AnswerColumn,
    AnswerForeignKey,
    AnswerFunctionalDependency,
    AnswerTable,
    BenchAnswer,
    BenchPrediction,
    PredictedColumn,
    PredictedTable,
)
from bench.baselines import (
    name_rule_baseline,
    position_break_baseline,
    wide_table_baseline,
)
from bench.criteria import (
    MINIMUM_MODEL_RATIO,
    REQUIRED_JSON_COMPLIANCE,
    GateStatus,
    RunMetrics,
    check_gates,
    classify_models,
)
from bench.generator import PACKS, build_dataset
from bench.scoring import (
    adjusted_rand_index,
    foreign_key_f1,
    functional_dependency_f1,
    functional_dependency_recall,
    primary_key_f1,
    schema_recovery_score,
    table_count_error,
    type_f1,
)
from pydantic import ValidationError

from autoetl.contracts import PhysicalType

# ── 데모 정답: 조인 키(a_id)는 평면에서 한 번 ───────────────────────────────


def demo_answer() -> BenchAnswer:
    return BenchAnswer(
        pack="demo",
        tables=[
            AnswerTable(
                name="a",
                primary_key=["a_id"],
                columns=[
                    AnswerColumn(name="a_id", column_id="a_id", physical_type=PhysicalType.STRING),
                    AnswerColumn(
                        name="a_name", column_id="a_name", physical_type=PhysicalType.STRING
                    ),
                    AnswerColumn(
                        name="a_amt", column_id="a_amt", physical_type=PhysicalType.DECIMAL
                    ),
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
                    AnswerColumn(name="b_id", column_id="b_id", physical_type=PhysicalType.STRING),
                    AnswerColumn(
                        name="a_id",
                        column_id="a_id",  # FK는 부모 PK와 같은 평면 컬럼
                        physical_type=PhysicalType.STRING,
                        is_foreign_key=True,
                    ),
                ],
                foreign_keys=[
                    AnswerForeignKey(
                        table="b",
                        columns=["a_id"],
                        references_table="a",
                        references_columns=["a_id"],
                    )
                ],
                functional_dependencies=[
                    AnswerFunctionalDependency(table="b", determinant=["b_id"], dependent="a_id")
                ],
                row_count=50,
            ),
        ],
        variant_headers={
            "clean": ["a_id", "a_name", "a_amt", "b_id"],
            "hard": ["col_01", "col_02", "col_03", "col_04"],
        },
    )


def perfect_prediction(answer: BenchAnswer, variant: str = "clean") -> BenchPrediction:
    """정답을 그대로 옮긴 예측. 컬럼 이름은 그 변형의 헤더 문자열이다."""
    header = answer.variant_headers[variant]
    column_ids: list[str] = []
    seen: set[str] = set()
    for table in answer.tables:
        for column in table.columns:
            if column.column_id not in seen:
                seen.add(column.column_id)
                column_ids.append(column.column_id)
    name_of = dict(zip(column_ids, header, strict=True))
    info: dict[str, tuple[PhysicalType, bool]] = {}
    for table in answer.tables:
        for column in table.columns:
            info.setdefault(column.column_id, (column.physical_type, column.is_foreign_key))
    tables: list[PredictedTable] = []
    for table in answer.tables:
        member_ids = list(dict.fromkeys(column.column_id for column in table.columns))
        n2i = {column.name: column.column_id for column in table.columns}
        tables.append(
            PredictedTable(
                columns=[
                    PredictedColumn(
                        name=name_of[column_id],
                        physical_type=info[column_id][0],
                        is_foreign_key=info[column_id][1],
                    )
                    for column_id in member_ids
                ],
                primary_key=[name_of[n2i[name]] for name in table.primary_key],
                foreign_keys=[
                    AnswerForeignKey(
                        table="",
                        columns=[name_of[n2i[name]] for name in fk.columns],
                        references_table="",
                        references_columns=fk.columns,
                    )
                    for fk in table.foreign_keys
                ],
                functional_dependencies=[
                    AnswerFunctionalDependency(
                        table="",
                        determinant=[name_of[n2i[name]] for name in fd.determinant],
                        dependent=name_of[n2i[fd.dependent]],
                    )
                    for fd in table.functional_dependencies
                ],
            )
        )
    return BenchPrediction(tables=tables)


# ── 만점 경로 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("variant", ["clean", "hard"])
def test_perfect_prediction_scores_full(variant: str) -> None:
    answer = demo_answer()
    prediction = perfect_prediction(answer, variant)
    assert type_f1(answer, prediction, variant) == pytest.approx(1.0)
    assert primary_key_f1(answer, prediction, variant) == pytest.approx(1.0)
    assert foreign_key_f1(answer, prediction, variant) == pytest.approx(1.0)
    assert functional_dependency_recall(answer, prediction, variant) == pytest.approx(1.0)
    assert adjusted_rand_index(answer, prediction, variant) == pytest.approx(1.0)
    assert table_count_error(answer, prediction) == 0
    assert schema_recovery_score(answer, prediction, variant) == pytest.approx(100.0)


def test_join_key_predicted_in_two_tables_counts_once_and_belongs_to_pk_owner() -> None:
    answer = demo_answer()
    prediction = perfect_prediction(answer)
    # 조인 키 a_id를 세 번째 테이블에 일반 컬럼으로 한 번 더 예측한 경우.
    prediction.tables.append(
        PredictedTable(
            columns=[
                PredictedColumn(name="a_id", physical_type=PhysicalType.STRING),
                PredictedColumn(name="ghost", physical_type=PhysicalType.STRING),
            ],
            primary_key=["ghost"],
        )
    )
    # 타입: a_id는 컬럼 ID당 한 번만 센다. 환각 ghost만 정밀도를 깎는다:
    # F1 = 2*(4/5)*1/((4/5)+1) = 8/9 (a_id를 두 번 세었다면 정밀도 4/6 → F1 0.8).
    assert type_f1(answer, prediction, "clean") == pytest.approx(8 / 9)
    # 배정: a_id는 PK로 쓴 테이블(a) 소속. ghost는 ARI가 벌점하지 않는다.
    assert adjusted_rand_index(answer, prediction, "clean") == pytest.approx(1.0)
    # PK: {a_id} 집합은 두 번 예측해도 같은 집합이라 중복으로 세지 않는다.
    # 환각 {ghost} PK만 정밀도를 깎는다: F1 = 2*(2/3)*1/((2/3)+1) = 0.8
    # (a_id를 두 번 세었다면 정밀도 2/4 → F1 0.667).
    assert primary_key_f1(answer, prediction, "clean") == pytest.approx(0.8)


def test_hallucinated_column_cuts_type_precision_not_ari() -> None:
    answer = demo_answer()
    prediction = perfect_prediction(answer)
    prediction.tables.append(
        PredictedTable(
            columns=[
                PredictedColumn(name="ghost", physical_type=PhysicalType.STRING),
            ],
            primary_key=["ghost"],
        )
    )
    # 환각 컬럼 ghost: 타입 정밀도는 깎지만(F1 8/9) ARI는 벌점하지 않는다.
    assert type_f1(answer, prediction, "clean") == pytest.approx(8 / 9)
    assert adjusted_rand_index(answer, prediction, "clean") == pytest.approx(1.0)


def test_one_wrong_type_drops_type_f1() -> None:
    answer = demo_answer()
    prediction = perfect_prediction(answer)
    # 4개 평면 컬럼 중 1개(a_amt) 타입을 틀리게 예측
    for table in prediction.tables:
        for column in table.columns:
            if column.name == "a_amt":
                column.physical_type = PhysicalType.INTEGER
    # 정밀도 3/4, 재현율 3/4 → F1 = 0.75
    assert type_f1(answer, prediction, "clean") == pytest.approx(0.75)


def test_unknown_variant_or_length_mismatch_raises() -> None:
    answer = demo_answer()
    prediction = perfect_prediction(answer)
    with pytest.raises(ValueError, match="헤더가 없다"):
        type_f1(answer, prediction, "format_mix")
    broken = answer.model_copy(deep=True)
    broken.variant_headers["clean"] = broken.variant_headers["clean"][:3]
    with pytest.raises(ValueError, match="길이"):
        type_f1(broken, prediction, "clean")


# ── ARI 커버리지(T-0014 규칙 유지) ──────────────────────────────────────────


def test_ari_single_common_column_is_scaled_by_coverage() -> None:
    answer = demo_answer()
    partial = BenchPrediction(
        tables=[
            PredictedTable(
                columns=[PredictedColumn(name="a_id", physical_type=PhysicalType.STRING)],
                primary_key=["a_id"],
            )
        ]
    )
    # 겹치는 컬럼 1개: 배정 비교는 자명 일치(1.0)지만 커버리지 1/4가 곱해진다.
    assert adjusted_rand_index(answer, partial, "clean") == pytest.approx(1.0 / 4)


def test_ari_scales_with_overlap_ratio() -> None:
    answer = demo_answer()
    # 테이블 a의 컬럼 2개만, 같은 테이블로 정확히 배정 → 기본 ARI 1.0에 커버리지 2/4를 곱한다
    partial = BenchPrediction(
        tables=[
            PredictedTable(
                columns=[
                    PredictedColumn(name="a_id", physical_type=PhysicalType.STRING),
                    PredictedColumn(name="a_name", physical_type=PhysicalType.STRING),
                ],
                primary_key=["a_id"],
            )
        ]
    )
    assert adjusted_rand_index(answer, partial, "clean") == pytest.approx(2.0 / 4)
    one_column = BenchPrediction(
        tables=[
            PredictedTable(
                columns=[PredictedColumn(name="a_id", physical_type=PhysicalType.STRING)],
                primary_key=["a_id"],
            )
        ]
    )
    assert adjusted_rand_index(answer, one_column, "clean") < adjusted_rand_index(
        answer, partial, "clean"
    )


def test_ari_full_coverage_prediction_beats_single_column_composite() -> None:
    # 재리뷰 재현: 컬럼 1개만 예측한 종합 점수가 전부 담은 예측을 앞서는 순위 뒤집임.
    answer = demo_answer()
    one_column = BenchPrediction(
        tables=[
            PredictedTable(
                columns=[PredictedColumn(name="a_id", physical_type=PhysicalType.STRING)],
                primary_key=["a_id"],
            )
        ]
    )
    merged_columns = [
        PredictedColumn(name=name, physical_type=PhysicalType.STRING)
        for name in answer.variant_headers["clean"]
    ]
    merged = BenchPrediction(tables=[PredictedTable(columns=merged_columns, primary_key=["a_id"])])
    assert schema_recovery_score(answer, one_column, "clean") < schema_recovery_score(
        answer, merged, "clean"
    )


# ── 빈 정답·스팸(T-0014 규칙 유지) ──────────────────────────────────────────


def test_empty_truth_fds_penalize_spam() -> None:
    fd_free = BenchAnswer(
        pack="demo",
        tables=[
            AnswerTable(
                name="solo",
                primary_key=["id"],
                columns=[
                    AnswerColumn(name="id", column_id="id", physical_type=PhysicalType.STRING)
                ],
                row_count=1,
            )
        ],
        variant_headers={"clean": ["id"]},
    )

    def solo_prediction(
        *,
        functional_dependencies: list[AnswerFunctionalDependency] | None = None,
        foreign_keys: list[AnswerForeignKey] | None = None,
    ) -> BenchPrediction:
        return BenchPrediction(
            tables=[
                PredictedTable(
                    columns=[PredictedColumn(name="id", physical_type=PhysicalType.STRING)],
                    primary_key=["id"],
                    functional_dependencies=functional_dependencies or [],
                    foreign_keys=foreign_keys or [],
                )
            ]
        )

    assert functional_dependency_recall(fd_free, solo_prediction(), "clean") == 1.0
    assert functional_dependency_f1(fd_free, solo_prediction(), "clean") == 1.0
    assert foreign_key_f1(fd_free, solo_prediction(), "clean") == 1.0

    spam_fds = [
        AnswerFunctionalDependency(table="", determinant=["id"], dependent="id") for _ in range(462)
    ]
    assert (
        functional_dependency_recall(
            fd_free, solo_prediction(functional_dependencies=spam_fds), "clean"
        )
        == 0.0
    )
    assert (
        functional_dependency_f1(
            fd_free, solo_prediction(functional_dependencies=spam_fds), "clean"
        )
        == 0.0
    )

    spam_fks = [
        AnswerForeignKey(table="", columns=["id"], references_table="", references_columns=["id"])
        for _ in range(30)
    ]
    assert foreign_key_f1(fd_free, solo_prediction(foreign_keys=spam_fks), "clean") == 0.0


def test_missing_table_lowers_ari_and_pk() -> None:
    answer = demo_answer()
    # 모든 컬럼을 한 테이블로 몰아 넣은(정규화 실패) 예측
    flat = perfect_prediction(answer)
    merged_columns = [column for table in flat.tables for column in table.columns]
    merged = BenchPrediction(
        tables=[
            PredictedTable(
                columns=merged_columns,
                primary_key=["a_id"],
                functional_dependencies=[],
            )
        ]
    )
    # 예측이 한 덩어리면 ARI = 0 (b_comb = c2(n)이 되어 분자가 0으로 수렴)
    assert adjusted_rand_index(answer, merged, "clean") == pytest.approx(0.0)
    # PK: 정담 2개 중 1개 재현, 예측 1개 → 정밀도 1, 재현율 .5
    assert primary_key_f1(answer, merged, "clean") == pytest.approx(2 * 0.5 / 1.5)
    assert table_count_error(answer, merged) == -1
    assert functional_dependency_recall(answer, merged, "clean") == 0.0
    assert schema_recovery_score(answer, merged, "clean") < 60.0


def test_dropped_fk_zeroes_fk_f1() -> None:
    answer = demo_answer()
    prediction = perfect_prediction(answer)
    prediction.tables[1].foreign_keys = []
    assert foreign_key_f1(answer, prediction, "clean") == 0.0


def test_answer_format_is_closed() -> None:
    answer = demo_answer()
    payload = answer.model_dump(mode="json")
    payload["tables"][0]["bogus"] = 1
    with pytest.raises(ValidationError):
        BenchAnswer.model_validate(payload)


def test_answer_version_bumped() -> None:
    assert ANSWER_VERSION == "0.2.0"


# ── 기준선(README 문서↔코드 일치) ──────────────────────────────────────────


@pytest.fixture(scope="module")
def dataset_answers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, BenchAnswer]:
    answers: dict[str, BenchAnswer] = {}
    for pack_name, pack in PACKS.items():
        out = tmp_path_factory.mktemp("bench") / f"{pack.name}-42"
        build_dataset(pack, seed=42, out_dir=out)
        answers[pack_name] = BenchAnswer.model_validate_json(
            (out / "answer.json").read_text(encoding="utf-8")
        )
    return answers


@pytest.fixture(scope="module")
def hard_header_rows(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[list[str]]]:
    """팩별 hard.csv의 헤더 두 행(그룹 행, 필드 행). 헤더 전용 공격 재료."""
    import csv

    rows_of: dict[str, list[list[str]]] = {}
    for pack_name, pack in PACKS.items():
        out = tmp_path_factory.mktemp("bench_hard") / f"{pack.name}-42"
        build_dataset(pack, seed=42, out_dir=out)
        with (out / "hard.csv").open(encoding="cp949", newline="") as handle:
            content = list(csv.reader(handle))
        rows_of[pack_name] = content[:2]
    return rows_of


def test_perfect_prediction_is_full_score_on_generated_packs(
    dataset_answers: dict[str, BenchAnswer],
) -> None:
    for answer in dataset_answers.values():
        for variant in ("clean", "hard"):
            prediction = perfect_prediction(answer, variant)
            assert schema_recovery_score(answer, prediction, variant) == pytest.approx(100.0)


def test_baselines_cannot_reach_full_score(dataset_answers: dict[str, BenchAnswer]) -> None:
    for answer in dataset_answers.values():
        for variant in ("clean", "hard"):
            wide = schema_recovery_score(answer, wide_table_baseline(answer, variant), variant)
            rule = schema_recovery_score(answer, name_rule_baseline(answer, variant), variant)
            posbreak = schema_recovery_score(
                answer, position_break_baseline(answer, variant), variant
            )
            assert wide < 50.0, "와이드 기준선이 지나치게 높으면 헤더 유출이다"
            assert rule < 60.0
            assert posbreak < 60.0, "위치 끊기가 60을 넘으면 이름 신호가 지나치게 세다"


def test_readme_baseline_table_matches_computed(
    dataset_answers: dict[str, BenchAnswer],
) -> None:
    readme = (Path(__file__).resolve().parents[1] / "bench" / "README.md").read_text(
        encoding="utf-8"
    )
    section = readme.split("### 기준선 점수")[1].split("##")[0]
    table_rows = [line for line in section.splitlines() if line.startswith("|")][2:]
    assert table_rows, "README에 기준선 표가 있어야 한다"
    baselines = {
        "단일 와이드": wide_table_baseline,
        "이름 규칙": name_rule_baseline,
        "위치 끊기": position_break_baseline,
    }
    for line in table_rows:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        pack_name, variant = cells[0], cells[1]
        answer = dataset_answers[pack_name]
        assert answer.pack == pack_name
        assert len(cells) == 2 + len(baselines), "표 열 수가 기준선 수와 같아야 한다"
        for label, factory in baselines.items():
            cell = cells[2 + list(baselines).index(label)]
            score = schema_recovery_score(answer, factory(answer, variant), variant)
            assert f"{score:.2f}" == cell, f"{pack_name}/{variant} {label} 기준선 불일치"


def test_hard_header_only_attacks_stay_below_clean_name_signal(
    dataset_answers: dict[str, BenchAnswer],
    hard_header_rows: dict[str, list[list[str]]],
) -> None:
    """그룹 행·필드 행만 읽는 위치 끊기 공격이 clean 이름 신호보다 낮아야 한다.

    소유자 재리뷰 재현('식별자' 위치 끊기 53.68점)의 재발 방지. 키 컬럼 라벨을
    '수치'로 통합한 뒤에도 '수치' 위치 끊기가 와이드 기준선을 넘는 잔여 신호가
    있어(orders_kr 47.87점) 현행 판정 기준은 clean 위치 끊기 미만으로 둔다.
    """
    for pack_name, answer in dataset_answers.items():
        group_row, field_row = hard_header_rows[pack_name]
        clean_signal = schema_recovery_score(
            answer, position_break_baseline(answer, "clean"), "clean"
        )
        wide = schema_recovery_score(answer, wide_table_baseline(answer, "hard"), "hard")

        def from_segments(segments: list[list[str]]) -> BenchPrediction:
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
                    if segment
                ]
            )

        attacks: dict[str, list[list[str]]] = {}
        # 공격 1: 라벨이 바뀌는 지점마다 끊는다(라벨런 세그먼트).
        segments: list[list[str]] = [[field_row[0]]]
        for i in range(1, len(group_row)):
            if group_row[i] != group_row[i - 1]:
                segments.append([])
            segments[-1].append(field_row[i])
        attacks["라벨런"] = segments
        # 공격 2: 라벨 값 하나를 골라 그 위치 앞에서마다 끊는다(구 '식별자' 공격의 일반화).
        for label in sorted(set(group_row)):
            starts = [i for i, value in enumerate(group_row) if value == label]
            bounds = sorted({0, *starts, len(field_row)})
            attacks[f"라벨 {label}"] = [
                field_row[start:end] for start, end in pairwise(bounds) if end > start
            ]
        for name, segments in attacks.items():
            score = schema_recovery_score(answer, from_segments(segments), "hard")
            assert score < clean_signal, (
                f"{pack_name} hard 헤더 전용 공격({name})이 clean 이름 신호"
                f"({clean_signal:.2f})를 넘었다: {score:.2f}"
            )
        assert wide < clean_signal


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
        run("clean", 8, 85),
    ]
    result = classify_models(runs)
    # 기준선은 관문 실패 모델(leaky 99)의 최고 점수이되, 후보는 clean뿐이다.
    # 최소 기준선 99*0.85=84.15 → clean(85) 자격 있음, 권장 94.05 → 없음.
    assert result.best_score == 99.0
    assert result.minimum_model == "clean"
    assert result.recommended_model is None


def test_classify_models_no_qualifier() -> None:
    result = classify_models([run("leaky", 8, 50, leak=2)])
    assert result.minimum_model is None
    assert result.recommended_model is None
    assert "관문" in result.reason
