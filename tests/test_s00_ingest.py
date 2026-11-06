"""S0 수집·정규화 추론 검증(T-0023) — 인코딩·구분자·헤더 행·픽스처·임시 파일."""

from __future__ import annotations

import tempfile
import tracemalloc
from pathlib import Path

import pytest
from bench.answer import BenchAnswer
from bench.generator import PACKS, build_dataset

from autoetl.cli import main as cli_main
from autoetl.core.context import RAW_TABLE, AnalysisContext
from autoetl.core.errors import InputError
from autoetl.core.pipeline import run_pipeline
from autoetl.core.stages.s00_ingest import probe_source

LEAF_ROWS = {"orders_kr": 900, "music": 800}


@pytest.fixture(scope="module")
def datasets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    out_by_pack: dict[str, Path] = {}
    for pack in PACKS.values():
        out = tmp_path_factory.mktemp("s0") / f"{pack.name}-42"
        build_dataset(pack, seed=42, out_dir=out)
        out_by_pack[pack.name] = out
    return out_by_pack


VARIANTS = ("clean", "missing", "format_mix", "leading_zero", "multi_header", "cp949", "hard")


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_every_variant_analyzes_with_exact_names_and_rows(
    pack_name: str, variant: str, datasets: dict[str, Path]
) -> None:
    # 완료 기준: 2팩x7변형 모두 종료 0, 컬럼 이름이 variant_headers와 정확히
    # 같고, 데이터 행 수가 정답 리프 테이블 행 수와 같다.
    answer = BenchAnswer.model_validate_json(
        (datasets[pack_name] / "answer.json").read_text(encoding="utf-8")
    )
    assert cli_main(["analyze", str(datasets[pack_name] / f"{variant}.csv"), "--no-llm"]) == 0
    result = run_pipeline(datasets[pack_name] / f"{variant}.csv")
    assert [column.name for column in result.ingest.columns] == answer.variant_headers[variant]
    assert result.ingest.row_count == LEAF_ROWS[pack_name]


@pytest.mark.parametrize("variant", ("cp949", "hard"))
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_cp949_family_encoding_recorded(
    pack_name: str, variant: str, datasets: dict[str, Path]
) -> None:
    # 완료 기준: 감지한 인코딩이 cp949 계열이고 IngestManifest에 기록된다.
    result = run_pipeline(datasets[pack_name] / f"{variant}.csv")
    encoding = result.ingest.files[0].encoding
    assert encoding.replace("-", "_").lower() in {"cp949", "euc_kr"}, encoding


@pytest.mark.parametrize("variant", ("multi_header", "hard"))
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_multi_row_header_count_is_two(
    pack_name: str, variant: str, datasets: dict[str, Path]
) -> None:
    # 완료 기준: header_row_count가 2고 다중 행 헤더 정규화가 기록된다.
    result = run_pipeline(datasets[pack_name] / f"{variant}.csv")
    assert result.ingest.header_row_count == 2
    assert result.ingest.applied_normalizations == ["다중 행 헤더 2행, 마지막 행 사용"]


def test_converted_temp_file_removed_after_run(datasets: dict[str, Path]) -> None:
    # 완료 기준: 실행 뒤 임시 파일이 남지 않는다(인코딩 변환 파일 포함).
    before = set(Path(tempfile.gettempdir()).glob("*.autoetl-utf8.csv"))
    run_pipeline(datasets["orders_kr"] / "cp949.csv")
    after = set(Path(tempfile.gettempdir()).glob("*.autoetl-utf8.csv"))
    assert after == before


# ── 픽스처: 구분자·인용·BOM·헤더 없음 ────────────────────────────────────────


def test_tsv_delimiter(tmp_path: Path) -> None:
    path = tmp_path / "data.tsv"
    path.write_text("id\tname\n1\t가나\n2\t다라\n", encoding="utf-8")
    result = run_pipeline(path)
    assert result.ingest.delimiter == "\t"
    assert [column.name for column in result.ingest.columns] == ["id", "name"]
    assert result.ingest.row_count == 2


def test_semicolon_delimiter(tmp_path: Path) -> None:
    path = tmp_path / "semi.csv"
    path.write_text("id;amount\n10;100\n20;200\n", encoding="utf-8")
    result = run_pipeline(path)
    assert result.ingest.delimiter == ";"
    assert result.ingest.row_count == 2


def test_quoted_newline_stays_one_record(tmp_path: Path) -> None:
    path = tmp_path / "quoted.csv"
    path.write_text('id,memo\n1,"줄바꿈\n둘째 줄"\n2,끝\n', encoding="utf-8")
    result = run_pipeline(path)
    assert result.ingest.row_count == 2  # 인용 안 줄바꿈은 행을 나누지 않는다
    with AnalysisContext(path) as ctx:
        rows = ctx.connection.execute(
            f"SELECT COUNT(*) FROM {RAW_TABLE} WHERE \"c1\" LIKE '%줄바꿈%'"
        ).fetchone()
        assert rows is not None and rows[0] == 1


def test_bom_file_header_clean(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_text("customer_id,amount\n1,100\n", encoding="utf-8-sig")
    spec = probe_source(path)
    assert spec.encoding == "utf-8"
    assert spec.header == ["customer_id", "amount"]
    assert not spec.converted


def test_headerless_numeric_file_gets_col_names_and_warning(tmp_path: Path) -> None:
    # 완료 기준 픽스처: 헤더 없는 숫자 파일 — col_1..N 명명 + 경고(SOP).
    path = tmp_path / "no_header.csv"
    path.write_text("1,100\n2,200\n3,300\n", encoding="utf-8")
    result = run_pipeline(path)
    assert result.ingest.header_row_count == 0
    assert [column.name for column in result.ingest.columns] == ["col_1", "col_2"]
    assert result.ingest.row_count == 3
    assert any("col_N" in warning for warning in result.ingest.warnings)


def test_empty_and_oversized_inputs_stay_input_errors(tmp_path: Path) -> None:
    # 오류 경로 유지: 빈 CSV·과대 셀은 InputError(값 없는 안내).
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(InputError, match="빈 CSV"):
        probe_source(empty)

    huge = tmp_path / "huge.csv"
    huge.write_text("a,b\n1," + "x" * 200_000 + "\n", encoding="utf-8")
    with pytest.raises(InputError, match="읽지 못했다"):
        probe_source(huge)
    # probe가 실패해도 임시 파일을 만들지 않았거나 지웠다.
    assert not list(Path(tempfile.gettempdir()).glob("*.autoetl-utf8.csv"))


def test_probe_is_deterministic(datasets: dict[str, Path]) -> None:
    first = probe_source(datasets["orders_kr"] / "hard.csv")
    second = probe_source(datasets["orders_kr"] / "hard.csv")
    if first.converted:
        first.load_path.unlink(missing_ok=True)
    if second.converted:
        second.load_path.unlink(missing_ok=True)
    assert first.encoding == second.encoding == "cp949"
    assert first.header == second.header
    assert first.row_count == second.row_count


# ── 리드 검토 반영: 헤더 오판 방지·맨 앞 빈 줄·CP949 감지 표본·메모리 ─────────


def test_first_data_row_mostly_blank_keeps_one_header_row(tmp_path: Path) -> None:
    # 리드 재현: 헤더 다음 첫 데이터 행의 숫자·날짜 칸 절반 이상이 비어 있어도
    # 데이터 행이 사라지지 않는다(빈 칸은 헤더 근거로 세지 않는다).
    path = tmp_path / "blank_first.csv"
    path.write_text(
        "id,qty,price,note\n1,,100,가\n2,5,200,나\n3,6,300,다\n4,7,400,라\n",
        encoding="utf-8",
    )
    spec = probe_source(path)
    assert spec.header_row_count == 1
    assert spec.header == ["id", "qty", "price", "note"]
    assert spec.row_count == 4


def test_single_numeric_column_missing_marker_keeps_one_header_row(tmp_path: Path) -> None:
    # 리드 재현: 숫자 컬럼이 하나뿐이고 첫 데이터 행의 그 칸이 결측 표기
    # 텍스트여도 1행 헤더를 유지한다(근거 칸 하한).
    path = tmp_path / "one_numeric.csv"
    lines = ["qty,label", "해당없음,가"] + [f"{value},값{value}" for value in range(2, 9)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    spec = probe_source(path)
    assert spec.header_row_count == 1
    assert spec.header == ["qty", "label"]
    assert spec.row_count == 8


def test_numeric_year_header_stays_one_header_row(tmp_path: Path) -> None:
    # 리드 재현(반대 방향): 연도형 숫자 헤더(2020, 2021…)는 '헤더 없음'이
    # 아니라 1행 헤더로 둔다 — 정수 헤더와 소수 데이터는 세부 형태가 다르다.
    path = tmp_path / "years.csv"
    lines = ["2020,2021,2022,2023"]
    lines += [f"{v + 0.5},{v + 0.25},{v + 0.5},{v + 0.75}" for v in range(12)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    spec = probe_source(path)
    assert spec.header_row_count == 1
    assert spec.header == ["2020", "2021", "2022", "2023"]
    assert spec.row_count == 12


def test_leading_blank_lines_do_not_eat_header(tmp_path: Path) -> None:
    # 리드 재현: 맨 앞 빈 줄(1~2개)이 있어도 헤더가 데이터로 적재되지 않는다
    # — skip은 헤더가 끝나는 물리 줄 위치이고, 적재 뒤 행 수를 대조한다.
    for blank_lines in (1, 2):
        path = tmp_path / f"lead_blank_{blank_lines}.csv"
        path.write_text(
            "\n" * blank_lines + "customer_id,amount\n1,100\n2,200\n3,300\n",
            encoding="utf-8",
        )
        result = run_pipeline(path)
        assert result.ingest.header_row_count == 1
        assert [column.name for column in result.ingest.columns] == ["customer_id", "amount"]
        assert result.ingest.row_count == 3
        with AnalysisContext(path) as ctx:
            rows = ctx.connection.execute(f"SELECT COUNT(*) FROM {RAW_TABLE}").fetchone()
            assert rows is not None and rows[0] == 3  # 매니페스트와 같다


def test_cp949_korean_after_long_ascii_head(tmp_path: Path) -> None:
    # 리드 재현: 앞 64KB가 ASCII이고 한글이 그 뒤에 처음 나오는 CP949 —
    # 감지 표본에 UTF-8 검사 실패 위치 근처를 포함해 cp949로 읽고 값이
    # 보존된다.
    head = ["id,memo"] + [
        f"{i},ascii-record-{i:05d}-padding-padding-padding-padding" for i in range(2200)
    ]
    tail = [f"{i},한글메모-{i}-거래처-{i}" for i in range(200)]
    path = tmp_path / "cp949_late_korean.csv"
    path.write_bytes(("\n".join(head + tail) + "\n").encode("cp949"))
    assert path.stat().st_size > 64 * 1024  # 한글이 표본 밖에 있다

    spec = probe_source(path)
    assert spec.encoding.replace("-", "_").lower() in {"cp949", "euc_kr"}
    assert spec.row_count == len(head) + len(tail) - 1
    if spec.converted:
        spec.load_path.unlink(missing_ok=True)
    with AnalysisContext(path) as ctx:
        rows = ctx.connection.execute(
            f"SELECT COUNT(*) FROM {RAW_TABLE} WHERE \"c1\" LIKE '%한글메모%'"
        ).fetchone()
        assert rows is not None and rows[0] == len(tail)  # 값이 깨지지 않았다


# ── 리드 재검토 #119 반영 — CP949 우선·1바이트 경고·연도+정수·결측 표기 ───────


def test_cp949_with_single_korean_value_wins_over_one_byte(tmp_path: Path) -> None:
    # 리드 재현: ASCII가 대부분(64KB 초과)이고 한글 값이 1개뿐인 CP949 —
    # 예전엔 cp1250 후보가 전체 파일 검증을 통과해 값이 조용히 깨졌다.
    # CP949 우선 규칙으로 cp949를 고르고 값이 보존된다(1바이트 경고도 없다).
    lines = ["id,city,memo"] + [f"{i},city-{i:05d},ascii-record-{i:05d}" for i in range(2200)]
    lines[1500] = "1500,city-01500,서울특별시 강남구"  # 한글 값 1개
    path = tmp_path / "cp949_sparse.csv"
    path.write_bytes(("\n".join(lines) + "\n").encode("cp949"))
    assert path.stat().st_size > 64 * 1024

    spec = probe_source(path)
    assert spec.encoding.replace("-", "_").lower() in {"cp949", "euc_kr"}
    assert not any("1바이트" in warning for warning in spec.warnings)
    if spec.converted:
        spec.load_path.unlink(missing_ok=True)
    with AnalysisContext(path) as ctx:
        rows = ctx.connection.execute(
            f"SELECT COUNT(*) FROM {RAW_TABLE} WHERE \"c2\" LIKE '%서울%'"
        ).fetchone()
        assert rows is not None and rows[0] == 1  # 값이 깨지지 않았다


def test_cp1252_file_is_not_cp949_and_warns(tmp_path: Path) -> None:
    # 리드 재현: 프랑스·독일어 문자가 든 cp1252 — cp949로 읽히지 않아야
    # 하고(리드가 서유럽 표본의 cp949 디코딩 실패를 확인했다), 1바이트
    # 코드페이지 판정에는 확인 요청 경고가 남는다.
    lines = ["id,name,city"] + [f"{i},Müller-{i},Saint-Étienne-{i}" for i in range(500)]
    path = tmp_path / "cp1252.csv"
    path.write_bytes(("\n".join(lines) + "\n").encode("cp1252"))

    spec = probe_source(path)
    assert "cp949" not in spec.encoding.lower() and "euc" not in spec.encoding.lower()
    assert any("1바이트" in warning for warning in spec.warnings), spec.warnings
    if spec.converted:
        spec.load_path.unlink(missing_ok=True)
    with AnalysisContext(path) as ctx:
        rows = ctx.connection.execute(
            f"SELECT COUNT(*) FROM {RAW_TABLE} WHERE \"c1\" LIKE '%Müller%'"
        ).fetchone()
        assert rows is not None and rows[0] == 500  # 악센트 문자 보존


def test_year_header_with_integer_data_stays_one_header_row(tmp_path: Path) -> None:
    # 리드 재현(#119): 연도형 숫자 헤더 + 인구 같은 큰 정수 데이터 — 둘 다
    # 정수라 세부 형태만으로 구분되지 않는다. 자릿수 비교로 1행을 유지한다.
    path = tmp_path / "years_int.csv"
    lines = ["지역,2020,2021,2022"]
    lines += [
        f"지역-{r:02d},{5_000_000 + r * 1111},{5_100_000 + r * 1111},{5_200_000 + r * 1111}"
        for r in range(12)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    spec = probe_source(path)
    assert spec.header_row_count == 1
    assert spec.header == ["지역", "2020", "2021", "2022"]
    assert spec.row_count == 12


def test_missing_markers_in_first_data_row_are_not_header_evidence(tmp_path: Path) -> None:
    # 리드 재현: 숫자 컬럼 2칸에 결측 표기('N/A', '-')가 든 첫 데이터 행이
    # 헤더로 판정돼 행이 사라지고 셀 값이 컬럼 이름이 되던 누출 경로 차단.
    path = tmp_path / "markers.csv"
    path.write_text(
        "id,qty,price,note\n1,N/A,-,가\n2,5,200,나\n3,6,300,다\n4,7,400,라\n",
        encoding="utf-8",
    )
    spec = probe_source(path)
    assert spec.header_row_count == 1
    assert spec.header == ["id", "qty", "price", "note"]
    assert spec.row_count == 4


def test_probe_memory_does_not_scale_with_file_size(tmp_path: Path) -> None:
    # 리드 재현: probe 메모리는 파일 크기와 무관해야 한다(30.6MB에서 381MB
    # 최고점이었다). 두 배 이상 크기가 다른 파일의 최고점이 같은 상한 아래다.
    def write_numeric(rows: int) -> Path:
        target = tmp_path / f"memory-{rows}.csv"
        lines = ["id,qty,price,amount,total"]
        lines += [f"{r},{r * 2},{r * 3},{r * 4},{r * 5}" for r in range(rows)]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    small = write_numeric(50_000)  # ≈ 1.5MB
    large = write_numeric(600_000)  # ≈ 20MB
    assert large.stat().st_size > 4 * small.stat().st_size  # 크기 차이가 의미 있게

    peaks: list[int] = []
    for target in (small, large):
        tracemalloc.start()
        try:
            probe_source(target)
        finally:
            peaks.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
    assert max(peaks) < 8 * 1024 * 1024  # 상한 8MB — 파일 크기(최대 20MB)보다 작다
