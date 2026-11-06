"""S3 타입 추론 검증(T-0025) — 완료 기준 전항 + 정규화 뷰 + 판정 규칙."""

from __future__ import annotations

import decimal
from datetime import date
from pathlib import Path
from typing import cast

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


# ── 리드 검토 반영 — 정수 반올림·표본 편향·시리얼 게이트·패딩 게이트 ─────────


def _run_s3(
    tmp_path: Path, name: str, header: str, rows: list[str], *, sample_limit: int = 1000
) -> tuple[TypeAssignment, list[object], dict[str, int]]:
    """S0~S3만 돌려 (판정, 정규화 값, 실패 수)를 돌려준다."""
    path = tmp_path / f"{name}.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    spec = s00_ingest.probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = s00_ingest.run(ctx, spec)
        profiles = s02_profile.run(ctx, ingest, s01_sampling.run(ctx))
        types = s03_types.run(ctx, ingest, profiles, sample_limit=sample_limit)
        values = ctx.connection.execute(f'SELECT "c0" FROM {ctx.normalized_view}').fetchall()
        failures = cast("dict[str, int]", ctx.stage_artifacts[FAILURES_KEY])
    return types[0], [row[0] for row in values], failures


def test_integer_normalization_does_not_round_decimals(tmp_path: Path) -> None:
    # 리드 재현 #1: 정수로 판정된 컬럼의 소수('.5')는 반올림되지 않고 NULL +
    # 실패 수로 드러난다(DuckDB TRY_CAST는 6으로 반올림했었다).
    rows = [str(i * 7 + 3) for i in range(200)]
    rows[50] = ".5"
    assignment, values, failures = _run_s3(tmp_path, "round", "value", rows)
    assert assignment.physical_type is PhysicalType.INTEGER
    assert values[50] is None  # 반올림 값(1 또는 0)이 아니다
    assert failures == {"value": 1}
    assert any("정규화 실패 1값" in note for note in assignment.notes)


def test_decimal_tail_after_thousand_integers_is_decimal(tmp_path: Path) -> None:
    # 리드 재현 #2: 정수 1500행 뒤 소수 100행 — 시드 고정 무작위 표본이 소수를
    # 보고(전수 확인까지 마쳐) decimal이 된다. 정수가 아니다.
    rows = [str(i * 3 + 1) for i in range(1500)] + [f"{i}.5" for i in range(100)]
    assignment, values, failures = _run_s3(tmp_path, "tail_dec", "value", rows)
    assert assignment.physical_type is PhysicalType.DECIMAL
    assert all(value is not None for value in values)
    assert failures == {"value": 0}


def test_text_tail_after_thousand_dates_is_not_date(tmp_path: Path) -> None:
    # 리드 재현 #2: 날짜 1000행 뒤 텍스트 84행(7.7%) — 앞쪽 표본만 보면
    # 적합률 1.0이지만 무작위 표본이 텍스트를 본다. date가 아니다.
    rows = [f"2025-{1 + i % 12:02d}-{1 + i % 28:02d}" for i in range(1000)]
    rows += ["가나다" for _ in range(84)]
    assignment, _, _ = _run_s3(tmp_path, "tail_txt", "memo_date", rows)
    assert assignment.physical_type is not PhysicalType.DATE


def test_unhinted_serial_range_integers_stay_integer(tmp_path: Path) -> None:
    # 리드 재현 #3: 이름 힌트 없는 2만~6만 정수(가격 등)는 날짜가 아니다.
    rows = [str(20000 + (i * 137) % 40000) for i in range(300)]
    assignment, _, _ = _run_s3(tmp_path, "serialish", "value", rows)
    assert assignment.physical_type is PhysicalType.INTEGER


def test_serial_only_date_with_name_hint(tmp_path: Path) -> None:
    # 리드 재현 #3: 시리얼뿐인 'order_date'는 이름 힌트로 날짜가 된다.
    rows = [str(20000 + (i * 137) % 40000) for i in range(300)]
    assignment, _, _ = _run_s3(tmp_path, "serial_date", "order_date", rows)
    assert assignment.physical_type is PhysicalType.DATE
    assert assignment.detector == "builtin.date"


def test_single_padded_value_keeps_column_integer(tmp_path: Path) -> None:
    # 리드 재현 #4: 정수 컬럼에 '07' 하나 — 패딩 근거(2개 이상 + 1%)가 안 돼
    # 정수를 유지한다(LPAD로 전 컬럼이 2자리가 되지 않는다).
    rows = [str(i % 50 + 1) for i in range(900)]
    rows[500] = "07"
    assignment, values, _ = _run_s3(tmp_path, "one_pad", "num", rows)
    assert assignment.physical_type is PhysicalType.INTEGER
    assert values[500] == 7  # 정수로 읽는다(패딩 복원 없음)


# ── 리드 재검토(2차) 반영 — 무손실 원칙·전수 정밀도·날짜 게이트 하한 ─────────


def test_code_pad_never_truncates(tmp_path: Path) -> None:
    # 리드 재현: 5자리 코드에 7자리 코드가 섞이면 폭(전수 최대 길이)이 7이
    # 되고, 긴 값이 잘리지 않는다(LPAD 절단 금지).
    rows = [f"{i:05d}" for i in range(300)] + ["1234567"] * 3
    assignment, values, failures = _run_s3(tmp_path, "code_pad", "code", rows)
    assert assignment.physical_type is PhysicalType.STRING
    assert values.count("1234567") == 3  # 잘리지 않고 그대로
    assert "0000042" in values  # 짧은 값은 전수 폭(7)으로 채운다
    assert failures == {"code": 0}


def test_decimal_normalization_is_exact_at_full_scale(tmp_path: Path) -> None:
    # 리드 재현: 소수부 12자리 값이 DECIMAL(38,10) 반올림으로 7.9E-9가 되던
    # 것 — scale을 전수 최대(12)로 정해 정확히 보존한다.
    rows = [f"{i}.25" for i in range(500)] + ["0.000000007919"]
    assignment, values, failures = _run_s3(tmp_path, "deep_scale", "amount", rows)
    assert assignment.physical_type is PhysicalType.DECIMAL
    assert assignment.scale == 12
    assert values[-1] == decimal.Decimal("0.000000007919")  # 반올림 아님
    assert failures == {"amount": 0}


def test_precision_comes_from_full_scan_not_sample(tmp_path: Path) -> None:
    # 리드 재현: 2,000행(정수.소수2자리)에 큰 값(precision 20·scale 6)이
    # 표본 밖에 있어도 전수 최댓값으로 보고한다. 정규화 값도 원래 값과 같다.
    rows = [f"{i}.25" for i in range(2000)] + ["12345678901234.123456"] * 3
    assignment, values, failures = _run_s3(tmp_path, "full_prec", "amount", rows)
    assert assignment.physical_type is PhysicalType.DECIMAL
    assert assignment.precision is not None and assignment.precision >= 20
    assert assignment.scale == 6
    assert values[-1] == decimal.Decimal("12345678901234.123456")
    assert failures == {"amount": 0}


def test_fractional_serial_is_failure_not_rounded_date(tmp_path: Path) -> None:
    # 리드 재현: 문자열 날짜+시리얼 혼재 컬럼의 '45000.5' — 시리얼 분기가
    # 정수 형태일 때만 탄다(반올림된 2023-03-16이 나오지 않는다).
    rows = ["2025-01-05", "2025-02-10"] + ["45824"] * 297 + ["45000.5"]
    assignment, values, failures = _run_s3(tmp_path, "frac_serial", "order_date", rows)
    assert assignment.physical_type is PhysicalType.DATE
    assert values[-1] is None
    assert failures == {"order_date": 1}


def test_single_text_date_does_not_open_serial_gate(tmp_path: Path) -> None:
    # 리드 재현: 이름 힌트 없는 2만~6만 정수 900행에 yyyyMMdd 값 하나 —
    # 게이트(문자열 날짜 2개 이상 + 1% 이상)가 열리지 않아 integer다.
    rows = [str(20000 + (i * 137) % 40000) for i in range(900)] + ["20240101"]
    assignment, _, _ = _run_s3(tmp_path, "gate1", "value", rows)
    assert assignment.physical_type is PhysicalType.INTEGER


# ── 리드 재검토(3차) 반영 — 하향 확정·통화 형태 게이트·scale 상한 ────────────


def test_full_scan_fallback_adopts_passing_candidate(tmp_path: Path) -> None:
    # 리드 재현: 정수 96% + 소수 4%인데 표본(20값, 시드 42)이 전부 정수다.
    # integer는 전수 확인에서 탈락(실패 800 > 허용 400)하고 decimal이 실패
    # 0으로 통과한다 — 통과한 decimal을 확정한다. 결함이 있으면 탈락한
    # integer(confirmation[0])를 다시 골라 '정규화 실패 800값'이 붙었다.
    rows = [str(i * 7 + 3) for i in range(19200)]
    rows += [f"{i}.5" for i in range(800)]
    assignment, values, failures = _run_s3(tmp_path, "fallback", "value", rows, sample_limit=20)
    assert assignment.physical_type is PhysicalType.DECIMAL
    assert assignment.detector == "builtin.decimal"
    assert failures == {"value": 0}
    assert all(value is not None for value in values)  # 소수 800개까지 전부 변환


def test_decimal_normalization_rejects_misplaced_symbols(tmp_path: Path) -> None:
    # 리드 재현: 소수점 쉼표 '1,5'(→15가 아니다)와 중간 통화 '12원34'
    # (→1234가 아니다)는 기호를 위치와 상관없이 지우면 값이 조용히 바뀐다.
    # 탐지기와 같은 통화 형태를 통과한 값만 변환하고 나머지는 NULL+실패.
    rows = [f"{i}.5" for i in range(200)]
    rows += ['"1,5"', '"12원34"', '"1,234,567원"', '"₩1,234"']  # 콤마는 CSV 인용
    assignment, values, failures = _run_s3(tmp_path, "won_mid", "amount", rows)
    assert assignment.physical_type is PhysicalType.DECIMAL
    assert values[200] is None  # '1,5' → 15 아님
    assert values[201] is None  # '12원34' → 1234 아님
    assert values[202] == decimal.Decimal("1234567")
    assert values[203] == decimal.Decimal("1234")
    assert failures == {"amount": 2}


def test_scale_38_column_does_not_crash(tmp_path: Path) -> None:
    # 리드 재현: 소수부 38자리 값이면 형태 정규식이 {1,0}이 돼 InvalidInput
    # 예외로 멈췄다. scale 상한 37로 두고 넘는 소수부는 NULL(실패)로 센다.
    # (정수부는 폭 38-37=1자리까지만 허용한다 — 그래서 1자리 값으로 맞춘다.)
    rows = [f"{i}.5" for i in range(10)] * 5 + ["0." + "1" * 38]
    assignment, values, failures = _run_s3(tmp_path, "scale38", "value", rows)
    assert assignment.physical_type is PhysicalType.DECIMAL
    assert assignment.scale == 37  # 38이 아니라 상한 37
    assert values[-1] is None  # 반올림·절단 없이 실패
    assert failures == {"value": 1}
