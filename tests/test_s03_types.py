"""S3 타입 추론 검증(T-0025) — 완료 기준 전항 + 정규화 뷰 + 판정 규칙."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from bench.answer import (
    BenchAnswer,
    BenchPrediction,
    PredictedColumn,
    PredictedTable,
)
from bench.generator import PACKS, build_dataset
from bench.scoring import type_f1

from autoetl.contracts import IngestManifest, PhysicalType, TypeAssignment
from autoetl.core.context import RAW_TABLE, AnalysisContext
from autoetl.core.pipeline import run_pipeline
from autoetl.core.stages import s00_ingest, s01_sampling, s02_profile, s03_types
from autoetl.core.stages.s02_profile import DEFAULT_MISSING_MARKERS
from autoetl.core.stages.s03_types import FAILURES_KEY

VARIANTS = ("clean", "missing", "format_mix", "leading_zero", "multi_header", "cp949", "hard")
COLUMN_IDS = ("release_date", "list_price", "catalog_no", "unit_price", "product_code")


@pytest.fixture(scope="module")
def datasets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    out_by_pack: dict[str, Path] = {}
    for pack in PACKS.values():
        out = tmp_path_factory.mktemp("s3") / f"{pack.name}-42"
        build_dataset(pack, seed=42, out_dir=out)
        out_by_pack[pack.name] = out
    return out_by_pack


def _types_of(path: Path) -> tuple[IngestManifest, list[TypeAssignment]]:
    result = run_pipeline(path)
    return result.ingest, result.types


def _prediction(ingest: IngestManifest, types: list[TypeAssignment]) -> BenchPrediction:
    return BenchPrediction(
        tables=[
            PredictedTable(
                columns=[
                    PredictedColumn(name=column.name, physical_type=assignment.physical_type)
                    for column, assignment in zip(ingest.columns, types, strict=True)
                ],
                primary_key=[ingest.columns[0].name],
            )
        ]
    )


# ── 완료 기준: 벤치 타입 F1 — clean 1.0, 나머지 변형 0.9 이상 ───────────────


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_type_f1_meets_targets(datasets: dict[str, Path], pack_name: str, variant: str) -> None:
    # 단위 채점: TypeAssignment → BenchPrediction을 직접 구성해 type_f1로 잰다
    # (후보 스키마(S9)에 타입을 잇는 것은 다음 과업 몫이다).
    out = datasets[pack_name]
    answer = BenchAnswer.model_validate_json((out / "answer.json").read_text(encoding="utf-8"))
    ingest, types = _types_of(out / f"{variant}.csv")
    f1 = type_f1(answer, _prediction(ingest, types), variant)
    if variant == "clean":
        assert f1 == 1.0
    else:
        assert f1 >= 0.9, f"{pack_name}/{variant}"


@pytest.mark.parametrize("variant", ("format_mix", "hard"))
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_trap_columns_typed_correctly(
    datasets: dict[str, Path], pack_name: str, variant: str
) -> None:
    # 완료 기준: 날짜 컬럼은 date, 금액 컬럼은 decimal, 앞자리 0 코드는 string.
    # 컬럼 위치는 변형이 달라도 같다 — clean 헤더로 위치를 잡는다(hard는 col_NN).
    out = datasets[pack_name]
    answer = BenchAnswer.model_validate_json((out / "answer.json").read_text(encoding="utf-8"))
    clean_headers = answer.variant_headers["clean"]
    variant_headers = answer.variant_headers[variant]
    truth: dict[str, PhysicalType] = {}
    for table in answer.tables:
        for column in table.columns:
            truth.setdefault(column.column_id, column.physical_type)

    ingest, types = _types_of(out / f"{variant}.csv")
    for column_id in COLUMN_IDS:
        if column_id not in clean_headers or column_id not in truth:
            continue
        position = clean_headers.index(column_id)
        assignment = types[position]
        assert ingest.columns[position].name == variant_headers[position]
        assert assignment.physical_type is truth[column_id], (pack_name, variant, column_id)


def test_decimal_point_and_comma_columns_are_not_integer(tmp_path: Path) -> None:
    # SOP 점검: 소수점·천 단위 콤마가 있으면 integer가 아니다.
    path = tmp_path / "decimals.csv"
    path.write_text('가격,단가\n1.5,"1,000"\n2.25,"2,500"\n', encoding="utf-8")
    _, types = _types_of(path)
    assert types[0].physical_type is PhysicalType.DECIMAL
    assert types[1].physical_type is PhysicalType.DECIMAL


def test_excel_serial_and_dense_date_normalize_equal(tmp_path: Path) -> None:
    # 완료 기준: 엑셀 시리얼(45824)과 yyyyMMdd(20250616)는 같은 날짜로 정규화된다.
    path = tmp_path / "dates.csv"
    path.write_text("d\n2025-06-16\n45824\n20250616\n2025/06/16\n2025.06.16\n", encoding="utf-8")
    spec = s00_ingest.probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = s00_ingest.run(ctx, spec)
        profiles = s02_profile.run(ctx, ingest, s01_sampling.run(ctx))
        types = s03_types.run(ctx, ingest, profiles)
        rows = ctx.connection.execute('SELECT "c0" FROM normalized').fetchall()
    assert types[0].physical_type is PhysicalType.DATE
    assert [row[0] for row in rows] == [date(2025, 6, 16)] * 5


# ── 완료 기준: 정규화 뷰 — 잡음 변형(missing 제외) 셀 일치 99% 이상 ──────────


_NORMALIZED_CACHE: dict[Path, tuple[list[tuple[object, ...]], list[tuple[object, ...]]]] = {}


def _normalized_and_raw(
    path: Path,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    """정규화 뷰 값과 원본 값(행 순서 대조용)을 함께 가져온다. 모듈 캐시로 재사용."""
    if path in _NORMALIZED_CACHE:
        return _NORMALIZED_CACHE[path]
    spec = s00_ingest.probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = s00_ingest.run(ctx, spec)
        profiles = s02_profile.run(ctx, ingest, s01_sampling.run(ctx))
        s03_types.run(ctx, ingest, profiles)
        normalized = ctx.connection.execute(f"SELECT * FROM {ctx.normalized_view}").fetchall()
        raw = ctx.connection.execute(f"SELECT * FROM {RAW_TABLE}").fetchall()
    _NORMALIZED_CACHE[path] = (normalized, raw)
    return normalized, raw


def _is_missing_raw(value: object) -> bool:
    return value is None or (
        isinstance(value, str) and (not value.strip() or value in DEFAULT_MISSING_MARKERS)
    )


@pytest.mark.parametrize("variant", ("format_mix", "leading_zero", "multi_header", "cp949", "hard"))
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_normalized_view_matches_clean(
    datasets: dict[str, Path], pack_name: str, variant: str
) -> None:
    # 변이의 값 있는 셀만 비교해 clean 정규화 값과의 일치율이 99% 이상이다.
    # 결측 표기 셀은 정규화 규칙대로 NULL이므로 비교에서 제외한다(원복 불가).
    out = datasets[pack_name]
    clean_norm, _ = _normalized_and_raw(out / "clean.csv")
    variant_norm, variant_raw = _normalized_and_raw(out / f"{variant}.csv")
    assert len(clean_norm) == len(variant_norm)

    width = len(clean_norm[0])
    for index in range(width):
        compared = equal = 0
        for clean_row, norm_row, raw_row in zip(clean_norm, variant_norm, variant_raw, strict=True):
            if _is_missing_raw(raw_row[index]):
                continue
            compared += 1
            if norm_row[index] == clean_row[index]:
                equal += 1
        assert compared > 0
        assert equal / compared >= 0.99, (pack_name, variant, index)


# ── 판정 규칙·메타데이터 ────────────────────────────────────────────────────


def test_same_input_yields_same_types(tmp_path: Path) -> None:
    path = tmp_path / "same.csv"
    path.write_text("id,amount,d\n1,1000,2025-01-01\n2,2000,2025-01-02\n", encoding="utf-8")
    assert _types_of(path)[1] == _types_of(path)[1]


def test_llm_resolved_false_and_ambiguity_note(tmp_path: Path) -> None:
    # P1(--no-llm)에서는 LLM 질의가 없다. 값 증거가 동률(정수 대 decimal)인
    # 컬럼은 '격차 미달' 노트가 붙고, 값 증거가 확정적인 컬럼은 노트가 없다.
    path = tmp_path / "ambig.csv"
    path.write_text(
        "q,amount,email\n1,45000,a@x.com\n2,46000,b@x.com\n3,47000,c@x.com\n",
        encoding="utf-8",
    )
    _, types = _types_of(path)
    by_name = {t.column: t for t in types}
    assert all(not t.llm_resolved for t in types)
    assert any("격차" in note for note in by_name["amount"].notes)  # 정수형 금액 동률
    assert not any("격차" in note for note in by_name["email"].notes)
    assert by_name["amount"].physical_type is PhysicalType.DECIMAL  # 이름 힌트 판결


def test_name_hint_cannot_overturn_value_evidence(tmp_path: Path) -> None:
    # 소수점이 섞인 금액(값 근거 확정)은 힌트가 없어도 decimal이고,
    # 힌트가 있어도 integer로 뒤집히지 않는다.
    path = tmp_path / "evidence.csv"
    path.write_text("amount,amount2\n45000.5,45000.5\n46000,46000.25\n", encoding="utf-8")
    _, types = _types_of(path)
    assert types[0].physical_type is PhysicalType.DECIMAL
    assert types[1].physical_type is PhysicalType.DECIMAL


def test_normalization_failure_counted_and_noted(tmp_path: Path) -> None:
    # 날짜 100개 + 이형 1개(적합률 100/101 ≈ 0.990 ≥ 0.98) → date로 확정되고
    # 정규화 실패 1값이 stage_artifacts와 notes에 집계로 남는다.
    lines = ["d"] + ["2025-01-01"] * 100 + ["안녕"]
    path = tmp_path / "onebad.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    spec = s00_ingest.probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = s00_ingest.run(ctx, spec)
        profiles = s02_profile.run(ctx, ingest, s01_sampling.run(ctx))
        types = s03_types.run(ctx, ingest, profiles)
        failures = ctx.stage_artifacts[FAILURES_KEY]
    assert types[0].physical_type is PhysicalType.DATE
    assert failures == {"d": 1}
    assert any("정규화 실패 1값" in note for note in types[0].notes)


def test_missing_markers_normalize_to_null(tmp_path: Path) -> None:
    path = tmp_path / "markers.csv"
    path.write_text("q,note\n5,가\nN/A,-\n7,NULL\n", encoding="utf-8")
    spec = s00_ingest.probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = s00_ingest.run(ctx, spec)
        profiles = s02_profile.run(ctx, ingest, s01_sampling.run(ctx))
        s03_types.run(ctx, ingest, profiles)
        rows = ctx.connection.execute(f'SELECT "c0", "c1" FROM {ctx.normalized_view}').fetchall()
    assert rows == [(5, "가"), (None, None), (7, None)]
