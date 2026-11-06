"""벤치 실행기 v0 검증(T-0022) — 변환기 정확성·실패 기록·결정론·표 형태."""

from __future__ import annotations

from pathlib import Path

import pytest
from bench.answer import BenchAnswer
from bench.generator import PACKS, build_dataset, flat_layout
from bench.generator.noise import VARIANTS
from bench.runner import (
    METRICS,
    VariantResult,
    markdown_table,
    run_bench,
    to_prediction,
)
from bench.scoring import schema_recovery_score

from autoetl.contracts import (
    PhysicalType,
    SchemaCandidate,
    SchemaPattern,
    TargetColumn,
    TargetTable,
)


@pytest.fixture(scope="module")
def answers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[BenchAnswer, Path]]:
    out_by_pack: dict[str, tuple[BenchAnswer, Path]] = {}
    for name, pack in PACKS.items():
        out = tmp_path_factory.mktemp("runner") / f"{pack.name}-42"
        build_dataset(pack, seed=42, out_dir=out)
        answer = BenchAnswer.model_validate_json((out / "answer.json").read_text(encoding="utf-8"))
        out_by_pack[name] = (answer, out)
    return out_by_pack


def _candidate_from_answer(answer: BenchAnswer, variant: str) -> SchemaCandidate:
    """정답을 SchemaCandidate로 옮긴 입력(완료 기준의 변환기 정확성 재료).

    source_columns는 그 변형의 필드 헤더 문자열(variant_headers)이고, 위치는
    평면 배치(flat_layout)의 컬럼 ID 위치에서 찾는다.
    """
    layout = flat_layout(PACKS[answer.pack])
    position = {column.column_id: i for i, column in enumerate(layout)}
    headers = answer.variant_headers[variant]
    tables: list[TargetTable] = []
    for table in answer.tables:
        foreign_key_of = {
            column: f"{fk.references_table}({fk.references_columns[0]})"
            for fk in table.foreign_keys
            for column in fk.columns
        }
        columns = [
            TargetColumn(
                name=column.name,
                physical_type=column.physical_type,
                source_columns=[headers[position[column.column_id]]],
                nullable=True,
                is_primary_key=column.name in table.primary_key,
                foreign_key=foreign_key_of.get(column.name),
            )
            for column in table.columns
        ]
        tables.append(TargetTable(name=table.name, columns=columns))
    return SchemaCandidate(
        id="C-answer",
        pattern=SchemaPattern.THIRD_NORMAL_FORM,
        tables=tables,
        rationale="정답 이식(변환기 검증용)",
    )


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_converter_scores_perfect_on_every_variant(
    pack_name: str, variant: str, answers: dict[str, tuple[BenchAnswer, Path]]
) -> None:
    # 완료 기준: 정답을 SchemaCandidate로 옮긴 입력을 변환해 채점하면 모든 변형에서
    # 100점이다(변환 규칙 — 이름·타입·PK·FK·FD를 잃지 않는다).
    answer, _ = answers[pack_name]
    prediction = to_prediction(_candidate_from_answer(answer, variant))
    assert schema_recovery_score(answer, prediction, variant) == pytest.approx(100.0)


def test_runner_on_current_main_records_failures_and_finishes(
    tmp_path: Path,
) -> None:
    # 완료 기준: 현재 main(와이드 스텁)에서 실행하면 표가 나온다. 못 읽는
    # 변형(cp949·hard)은 실패로 기록되고 실행기는 끝까지 돈다.
    results = run_bench(seed=42, datasets_dir=tmp_path)
    assert len(results) == len(PACKS) * len(VARIANTS)
    for item in results:
        assert isinstance(item, VariantResult)
        if item.variant in ("cp949", "hard"):
            assert item.metrics is None
            assert item.failure_stage == "S0"
            assert item.failure_exit_code == 1
            assert item.candidate_label == "실패"
        else:
            assert item.metrics is not None
            assert set(item.metrics) >= set(METRICS) | {"fd_recall"}
            assert item.failure_stage is None
            # v0에는 3NF 후보가 없다 — 선택 후보로 채점하고 표에 표시한다.
            assert item.candidate_label == "선택 후보(C1)"
            assert item.selected_total == item.metrics["total"]


def test_same_seed_produces_identical_results(tmp_path: Path) -> None:
    # 완료 기준: 같은 시드로 두 번 돌리면 결과가 같다(표·JSON 모두).
    first = run_bench(seed=7, datasets_dir=tmp_path / "a")
    second = run_bench(seed=7, datasets_dir=tmp_path / "b")
    assert markdown_table(first, seed=7) == markdown_table(second, seed=7)
    assert [item.to_json() for item in first] == [item.to_json() for item in second]


def test_markdown_table_shape() -> None:
    # 점수표: 팩x변형 행, 지표 열(타입·PK·FK·FD·ARI·종합), 채점 대상 표시.
    ok = VariantResult(
        pack="orders_kr",
        variant="clean",
        candidate_label="3nf(C2)",
        metrics=dict.fromkeys(METRICS, 0.5) | {"fd_recall": 0.5},
        selected_total=0.25,
        failure_stage=None,
        failure_exit_code=None,
    )
    failed = VariantResult(
        pack="orders_kr",
        variant="cp949",
        candidate_label="실패",
        metrics=None,
        selected_total=None,
        failure_stage="S0",
        failure_exit_code=1,
    )
    table = markdown_table([ok, failed], seed=42)
    lines = [line for line in table.splitlines() if line.startswith("|")]
    assert len(lines) == 4  # 헤더 2행 + 결과 2행
    cells = lines[0].strip("|").split("|")
    assert [cell.strip() for cell in cells] == [
        "팩",
        "변형",
        "타입 F1",
        "PK F1",
        "FK F1",
        "FD F1",
        "ARI",
        "종합",
        "채점 대상",
        "선택 후보 종합",
    ]
    assert "3nf(C2)" in lines[2] and "0.25" in lines[2]
    assert "실패(S0, 종료 1)" in lines[3]


def test_converter_rejects_malformed_foreign_key() -> None:
    # 계약 위반 참조는 조용히 무시하지 않고 변환 오류를 낸다.
    candidate = SchemaCandidate(
        id="C1",
        pattern=SchemaPattern.WIDE,
        tables=[
            TargetTable(
                name="wide_table",
                columns=[
                    TargetColumn(
                        name="a",
                        physical_type=PhysicalType.STRING,
                        source_columns=["a"],
                        nullable=True,
                        foreign_key="bad-format",
                    )
                ],
            )
        ],
        rationale="검증용",
    )
    with pytest.raises(ValueError, match="foreign_key 형식"):
        to_prediction(candidate)
