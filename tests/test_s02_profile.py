"""S2 구조 프로파일 검증(T-0024) — 결측·고유값·길이·형태 마스크·표본 모드."""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pytest
from bench.generator import PACKS, build_dataset

from autoetl.contracts import ColumnProfile, SamplingPlan
from autoetl.core.context import AnalysisContext
from autoetl.core.pipeline import run_pipeline
from autoetl.core.stages.s00_ingest import probe_source
from autoetl.core.stages.s00_ingest import run as run_ingest
from autoetl.core.stages.s01_sampling import run as run_sampling
from autoetl.core.stages.s02_profile import (
    DEFAULT_MISSING_MARKERS,
    SAMPLE_NOTE,
    _exact_or_approx_unique,
    pattern_mask,
    run,
)


@pytest.fixture(scope="module")
def datasets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    out_by_pack: dict[str, Path] = {}
    for pack in PACKS.values():
        out = tmp_path_factory.mktemp("s2") / f"{pack.name}-42"
        build_dataset(pack, seed=42, out_dir=out)
        out_by_pack[pack.name] = out
    return out_by_pack


def _profiles(path: Path) -> list[ColumnProfile]:
    return run_pipeline(path).profiles


# ── 완료 기준: missing 변형의 결측 수가 생성기가 넣은 수와 같다 ───────────────


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_missing_variant_counts_match_injected(datasets: dict[str, Path], pack_name: str) -> None:
    # clean과 cell 단위로 비교해 주입된 결측 수를 계산하고 missing_count와 대조한다.
    out = datasets[pack_name]
    with (out / "clean.csv").open(encoding="utf-8", newline="") as handle:
        clean_rows = list(csv.reader(handle))
    with (out / "missing.csv").open(encoding="utf-8", newline="") as handle:
        missing_rows = list(csv.reader(handle))
    header = clean_rows[0]

    injected = [0] * len(header)
    for clean_row, missing_row in zip(clean_rows[1:], missing_rows[1:], strict=True):
        for index, value in enumerate(missing_row):
            if value in DEFAULT_MISSING_MARKERS or not value.strip():
                assert clean_row[index].strip()  # 주입은 값 있던 칸에만
                injected[index] += 1

    profiles = _profiles(out / "missing.csv")
    by_name = {profile.column: profile for profile in profiles}
    for index, name in enumerate(header):
        # PK·조인 키 컬럼은 생성기가 결측을 넣지 않는다(둘 다 0이어야 한다).
        assert by_name[name].missing_count == injected[index], name


# ── 완료 기준: 마스크에 원문 글자가 없고 비율 합이 1 이하 ─────────────────────


def _mask_char_is_safe(ch: str) -> bool:
    return ch in {"9", "A", "a", "가", "X"} or not ch.isalnum()


@pytest.mark.parametrize("variant", ("clean", "missing", "format_mix"))
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_patterns_bounded_and_masks_have_no_raw_text(
    datasets: dict[str, Path], pack_name: str, variant: str
) -> None:
    # 완료 기준: 모든 컬럼에서 비율 합 ≤ 1, 마스크에 영문·한글·숫자 원문이 없다.
    profiles = _profiles(datasets[pack_name] / f"{variant}.csv")
    assert profiles
    for profile in profiles:
        assert sum(pattern.rate for pattern in profile.top_patterns) <= 1.0 + 1e-9
        for pattern in profile.top_patterns:
            assert pattern.mask and all(_mask_char_is_safe(ch) for ch in pattern.mask)
        # 상위 개수는 기본 5개를 넘지 않는다.
        assert len(profile.top_patterns) <= 5


# ── 완료 기준: 같은 입력은 같은 프로파일 ────────────────────────────────────


def test_same_input_yields_same_profile(tmp_path: Path) -> None:
    path = tmp_path / "same.csv"
    path.write_text(
        "id,name,note\n1,홍길동,가나\n2,김철수,다라\n3,N/A,-\n4,홍길동,마바\n",
        encoding="utf-8",
    )
    assert _profiles(path) == _profiles(path)


def test_pipeline_profiles_are_real_not_stub(tmp_path: Path) -> None:
    # 스텁(통계 0)이 아니라 실제 집계가 나온다.
    path = tmp_path / "real.csv"
    path.write_text("id,city\n1,서울\n2,부산\n3,서울\n", encoding="utf-8")
    result = run_pipeline(path)
    assert result.sampling.strategy == "full"
    id_profile, city_profile = result.profiles
    assert id_profile.count == 3 and id_profile.missing_count == 0
    assert id_profile.unique_count == 3 and id_profile.cardinality_ratio == 1.0
    assert city_profile.unique_count == 2
    assert city_profile.top_patterns[0].mask == "가가"


# ── 결측 표기 집합 ──────────────────────────────────────────────────────────


def test_default_missing_markers_match_spec() -> None:
    # 카드 결정: 빈 문자열·공백만(조건 처리) + 고정 목록(정확 일치).
    expected = frozenset({"-", "N/A", "n/a", "NULL", "null", "해당없음"})
    assert expected == DEFAULT_MISSING_MARKERS


def test_null_empty_and_whitespace_are_missing(tmp_path: Path) -> None:
    # NULL(따옴표 없는 빈 칸), ''(따옴표 빈 문자열), '   '(공백만)은 모두 결측이다.
    path = tmp_path / "blanks.csv"
    path.write_text('a,b\n,x\n"",y\n   ,z\n값,w\n', encoding="utf-8")
    spec = probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = run_ingest(ctx, spec)
        profiles = run(ctx, ingest, SamplingPlan(strategy="full", total_rows=4, rationale="전수"))
    assert profiles[0].missing_count == 3
    assert profiles[0].count == 1
    assert profiles[1].missing_count == 0


def test_missing_marker_set_is_configurable(tmp_path: Path) -> None:
    # 마커 집합을 바꾸면 그 집합으로만 판정한다('-'만 결측, 'NULL'은 값).
    path = tmp_path / "markers.csv"
    path.write_text("a,b\n-,NULL\n-,NULL\n값,값\n", encoding="utf-8")
    spec = probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = run_ingest(ctx, spec)
        profiles = run(
            ctx,
            ingest,
            SamplingPlan(strategy="full", total_rows=3, rationale="전수"),
            missing_markers={"-"},
        )
    assert profiles[0].missing_count == 2
    assert profiles[1].missing_count == 0
    assert profiles[1].unique_count == 2  # 'NULL'은 값으로 센다


def test_marker_match_is_exact_not_substring(tmp_path: Path) -> None:
    # 마커는 값 전체와 정확히 같아야 한다(' N/A', 'N/A '는 값이다).
    path = tmp_path / "exact.csv"
    path.write_text("a,b\n N/A,x\nN/A ,y\nN/A,z\n", encoding="utf-8")
    spec = probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = run_ingest(ctx, spec)
        profiles = run(ctx, ingest, SamplingPlan(strategy="full", total_rows=3, rationale="전수"))
    assert profiles[0].missing_count == 1
    assert profiles[0].unique_count == 2  # ' N/A'와 'N/A '는 서로 다른 값


def test_all_missing_column_has_zero_stats(tmp_path: Path) -> None:
    path = tmp_path / "all_missing.csv"
    path.write_text("a,b\nN/A,1\nNULL,2\n해당없음,3\n", encoding="utf-8")
    result = run_pipeline(path)
    assert result.profiles[0].count == 0
    assert result.profiles[0].missing_count == 3
    assert result.profiles[0].missing_rate == 1.0
    assert result.profiles[0].cardinality_ratio == 0.0
    assert result.profiles[0].length.minimum == 0
    assert result.profiles[0].top_patterns == []


# ── 고유값·길이 통계 ────────────────────────────────────────────────────────


def test_unique_count_and_cardinality_ratio(tmp_path: Path) -> None:
    path = tmp_path / "uniq.csv"
    path.write_text("id,city,grade\n1,서울,A\n2,부산,A\n3,서울,B\n4,서울,A\n", encoding="utf-8")
    profiles = run_pipeline(path).profiles
    assert [p.unique_count for p in profiles] == [4, 2, 2]
    assert profiles[0].cardinality_ratio == 1.0
    assert profiles[1].cardinality_ratio == 0.5


def test_length_stats_values(tmp_path: Path) -> None:
    path = tmp_path / "len.csv"
    path.write_text("v\nab\nabcd\nabcdef\n", encoding="utf-8")
    length = run_pipeline(path).profiles[0].length
    assert length.minimum == 2
    assert length.maximum == 6
    assert length.mean == pytest.approx(4.0)
    assert length.p50 == pytest.approx(4.0)


def test_approx_fallback_on_memory_limit() -> None:
    # 전수 정확값이 메모리 한도(OutOfMemoryException)에 걸리면 근사 + 표시.
    def run_exact() -> int:
        raise duckdb.OutOfMemoryException("메모리 한도 초과 시험")

    def run_approx() -> int:
        return 123

    count, approximated = _exact_or_approx_unique(run_exact, run_approx)
    assert (count, approximated) == (123, True)
    count, approximated = _exact_or_approx_unique(lambda: 456, run_approx)
    assert (count, approximated) == (456, False)


# ── 형태 마스크 ─────────────────────────────────────────────────────────────


def test_pattern_mask_alphabet() -> None:
    assert pattern_mask("2020-01-02") == "9999-99-99"
    assert pattern_mask("010-1234-5678") == "999-9999-9999"
    assert pattern_mask("Abc한글123") == "Aaa가가999"
    assert pattern_mask("漢字かな") == "XXXX"  # 그 밖의 글자
    assert pattern_mask("a b,c.d") == "a a,a.a"  # 구두점·공백은 그대로
    assert pattern_mask("가나다") == "가가가"
    assert pattern_mask("user@mail.com") == "aaaa@aaaa.aaa"
    assert pattern_mask("") == ""


# ── 표본 모드 ───────────────────────────────────────────────────────────────


def test_sample_mode_keeps_full_aggregates_with_note(tmp_path: Path) -> None:
    # 표본 모드: 결측 수·고유값 수는 전수(전수 실행과 같다), 길이·형태는 표본,
    # notes에 표본 기반이라고 남는다.
    path = tmp_path / "sample_mode.csv"
    lines = ["id,city,note"]
    lines += [f"{i},도시{i % 7},메모-{i}" for i in range(60)]
    lines[10] = "10,N/A,메모-10"  # 결측 1개 주입
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    full_result = run_pipeline(path)
    spec = probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = run_ingest(ctx, spec)
        sampling = run_sampling(ctx, full_scan_limit=10, sample_limit=20)
        profiles = run(ctx, ingest, sampling)

    assert sampling.strategy == "sample"
    full_by_name = {p.column: p for p in full_result.profiles}
    for profile in profiles:
        full = full_by_name[profile.column]
        assert profile.missing_count == full.missing_count  # 전수 집계
        assert profile.count == full.count
        assert profile.unique_count == full.unique_count
        assert SAMPLE_NOTE in profile.notes  # 표본 기반 표시
        assert sum(pattern.rate for pattern in profile.top_patterns) <= 1.0 + 1e-9
