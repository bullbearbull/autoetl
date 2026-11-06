"""빈 파이프라인 러너 v0 — 벤치 v0 위에서의 종주 검증."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest
from bench.generator import PACKS, build_dataset

from autoetl.cli import main as cli_main
from autoetl.contracts import ARTIFACTS, Report, ReportSectionId
from autoetl.core.pipeline import SOP_VERSION, InputError, run_pipeline


@pytest.fixture(scope="module")
def bench_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pack = PACKS["orders_kr"]
    out = tmp_path_factory.mktemp("bench") / f"{pack.name}-42"
    build_dataset(pack, seed=42, out_dir=out)
    return out


# ── T-0021 리팩터링 전후 산출물 일치(골든 대조) ────────────────────────────────

GOLDEN_DIR = Path(__file__).parent / "golden" / "p1_engine_skeleton"
GOLDEN_VARIANTS = ("clean", "missing", "format_mix", "leading_zero", "multi_header")


@pytest.fixture(scope="module")
def golden_bench_dirs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    out_by_pack: dict[str, Path] = {}
    for pack in PACKS.values():
        out = tmp_path_factory.mktemp("golden") / f"{pack.name}-42"
        build_dataset(pack, seed=42, out_dir=out)
        out_by_pack[pack.name] = out
    return out_by_pack


@pytest.mark.parametrize("variant", GOLDEN_VARIANTS)
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_report_matches_golden(
    pack_name: str, variant: str, golden_bench_dirs: dict[str, Path]
) -> None:
    # 골든은 엔진 골격 분리 전(단일 pipeline.py) 코드가 시드 42 데이터셋에서 뽑은
    # Report다. 리팩터링은 산출물을 바꾸지 않아야 한다 — 생성 시각만 제외하고
    # 전체 JSON이 같아야 한다(T-0021 완료 기준).
    result = run_pipeline(golden_bench_dirs[pack_name] / f"{variant}.csv")
    payload = json.loads(result.report.model_dump_json())
    payload.pop("generated_at")
    golden = json.loads(
        (GOLDEN_DIR / f"{pack_name}-{variant}.report.json").read_text(encoding="utf-8")
    )
    assert payload == golden


@pytest.fixture(scope="module")
def bench_csv(bench_dir: Path) -> Path:
    return bench_dir / "clean.csv"


def test_sop_version_matches_sop_source() -> None:
    import yaml

    sop = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "sop" / "v1.yaml").read_text(encoding="utf-8")
    )
    assert sop["version"] == SOP_VERSION


def test_pipeline_validates_every_artifact(bench_csv: Path) -> None:
    result = run_pipeline(bench_csv)
    artifacts: list[tuple[str, object]] = [
        ("ingest_manifest", result.ingest),
        ("sampling_plan", result.sampling),
        ("column_profile", result.profiles[0]),
        ("type_assignment", result.types[0]),
        ("column_stats", result.stats[0]),
        ("relation_graph", result.relations),
        ("quality_report", result.quality),
        ("sensitivity_map", result.sensitivity),
        ("semantic_model", result.semantic),
        ("schema_candidate", result.candidates[0]),
        ("scorecard", result.scorecards[0]),
        ("recommendation", result.recommendation),
        ("selected_schema", result.selected),
        ("load_plan", result.load_plan),
        ("report", result.report),
    ]
    for name, artifact in artifacts:
        assert isinstance(artifact, ARTIFACTS[name]), name
        # JSON 직렬화·역직렬화가 계약을 통과하는지(=유효 계약 인스턴스).
        round_trip = ARTIFACTS[name].model_validate_json(artifact.model_dump_json())
        assert round_trip is not None


def test_pipeline_shapes_match_input(bench_csv: Path) -> None:
    with bench_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = sum(1 for _ in reader)

    result = run_pipeline(bench_csv)
    assert result.ingest.row_count == rows
    assert [column.name for column in result.ingest.columns] == header
    assert len(result.profiles) == len(header)
    assert len(result.types) == len(header)
    assert len(result.stats) == len(header)
    assert len(result.candidates) == 1
    wide = result.candidates[0].tables[0]
    assert len(wide.columns) == len(header)
    assert len(result.load_plan.mappings) == len(header)
    assert result.load_plan.expected_row_count == rows
    assert result.selected.candidate_id == result.recommendation.recommended_id


def test_report_has_standard_sections(bench_csv: Path) -> None:
    report = run_pipeline(bench_csv).report
    assert [section.id for section in report.sections] == list(ReportSectionId)
    assert report.sop_version == SOP_VERSION
    assert report.pipeline_version


def test_cli_writes_report_file(
    bench_csv: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "report.json"
    code = cli_main(["analyze", str(bench_csv), "--no-llm", "--out", str(out)])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert Report.model_validate(payload)
    assert payload["sop_version"] == SOP_VERSION


def test_cli_stdout_reports_json(bench_csv: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli_main(["analyze", str(bench_csv), "--no-llm"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    Report.model_validate(payload)


def test_cli_rejects_llm_mode(bench_csv: Path) -> None:
    assert cli_main(["analyze", str(bench_csv)]) == 2


def test_cli_rejects_missing_file(tmp_path: Path) -> None:
    assert cli_main(["analyze", str(tmp_path / "none.csv"), "--no-llm"]) == 2


def test_cli_fails_cleanly_on_cp949(bench_dir: Path) -> None:
    # v0은 UTF-8만: CP949 입력은 안내 문구와 함께 종료 코드 1로 깔끔히 실패한다.
    assert cli_main(["analyze", str(bench_dir / "cp949.csv"), "--no-llm"]) == 1


def test_pipeline_rejects_empty_csv(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(InputError, match="빈 CSV"):
        run_pipeline(empty)


def test_cli_fails_cleanly_on_oversized_cell(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # csv 필드 한도(131072자)를 넘는 셀: 안내 문구와 함께 종료 1, 셀 값은 새지 않는다.
    secret = "비밀마커값" + "x" * 200_000
    csv_path = tmp_path / "huge.csv"
    csv_path.write_text(f"a,b\n1,{secret}\n", encoding="utf-8")
    code = cli_main(["analyze", str(csv_path), "--no-llm"])
    captured = capsys.readouterr()
    assert code == 1
    assert "읽지 못했다" in captured.err
    assert "Traceback" not in captured.err
    assert "비밀마커값" not in captured.err


def test_cli_hides_value_bearing_internal_errors(
    bench_csv: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 이후 단계가 값을 담은 ValueError를 던져도 고정 문구만 나가고 종료 3이다.
    def explode(path: Path, *, purpose: object = None) -> None:
        raise ValueError(f"내부 값 노출: 고객A 010-1234-5678 {path}")

    monkeypatch.setattr("autoetl.cli.run_pipeline", explode)
    code = cli_main(["analyze", str(bench_csv), "--no-llm"])
    captured = capsys.readouterr()
    assert code == 3
    assert "내부 오류" in captured.err
    assert "010-1234-5678" not in captured.err


def test_pipeline_handles_korean_headers(tmp_path: Path) -> None:
    # 한글 헤더는 ASCII 표준 이름으로 변환되어야 계약(^[a-z][a-z0-9_]*$)을 통과한다.
    csv_path = tmp_path / "korean.csv"
    csv_path.write_text("고객명,주문금액\n홍길동,1000\n", encoding="utf-8")
    result = run_pipeline(csv_path)
    standard_names = [meaning.standard_name for meaning in result.semantic.column_meanings]
    target_names = [column.name for column in result.candidates[0].tables[0].columns]
    assert all(name[0].isascii() for name in standard_names + target_names)
    assert len(target_names) == len(set(target_names)), "정규화 이름은 유일해야 한다"


def test_pipeline_uniquifies_colliding_names(tmp_path: Path) -> None:
    csv_path = tmp_path / "collide.csv"
    csv_path.write_text("foo-bar,foo bar\n1,2\n", encoding="utf-8")
    result = run_pipeline(csv_path)
    target_names = [column.name for column in result.candidates[0].tables[0].columns]
    assert target_names == ["foo_bar", "foo_bar_2"]


def test_pipeline_uniquifies_duplicate_source_headers(tmp_path: Path) -> None:
    # 원본 헤더 자체가 겹치면: 소스 이름은 발생 접미(#N)로, 대상 이름은 위치 기반으로 유일.
    csv_path = tmp_path / "dup.csv"
    csv_path.write_text("a,a,b\n1,2,3\n", encoding="utf-8")
    result = run_pipeline(csv_path)
    assert [column.name for column in result.ingest.columns] == ["a", "a#2", "b"]
    assert [mapping.source_column for mapping in result.load_plan.mappings] == ["a", "a#2", "b"]
    target_names = [column.name for column in result.candidates[0].tables[0].columns]
    assert target_names == ["a", "a_2", "b"]
    assert len(set(target_names)) == len(target_names)


def test_pipeline_uniquifies_duplicate_korean_headers(tmp_path: Path) -> None:
    csv_path = tmp_path / "dup_kr.csv"
    csv_path.write_text("고객명,고객명\n홍길동,김철수\n", encoding="utf-8")
    result = run_pipeline(csv_path)
    assert [column.name for column in result.ingest.columns] == ["고객명", "고객명#2"]
    target_names = [column.name for column in result.candidates[0].tables[0].columns]
    assert target_names == ["col_1", "col_2"]


def test_pipeline_dedup_survives_suffix_collision(tmp_path: Path) -> None:
    # 접미를 붙인 이름이 실제 헤더와 겹쳐도 다음 접미로 밀어 유일성을 지킨다.
    csv_path = tmp_path / "tricky.csv"
    csv_path.write_text("a,a,a#2\n1,2,3\n", encoding="utf-8")
    result = run_pipeline(csv_path)
    sources = [column.name for column in result.ingest.columns]
    assert len(sources) == len(set(sources)) == 3


def test_pipeline_handles_empty_header_filling_collision(tmp_path: Path) -> None:
    # 빈 헤더를 col_1로 채웠더니 실제 col_1과 겹치는 경우(소유자 재리뷰 재현).
    csv_path = tmp_path / "fill.csv"
    csv_path.write_text(",col_1\n1,2\n", encoding="utf-8")
    result = run_pipeline(csv_path)
    assert [column.name for column in result.ingest.columns] == ["col_1", "col_1#2"]
    target_names = [column.name for column in result.candidates[0].tables[0].columns]
    assert target_names == ["col_1", "col_1_2"]


def test_pipeline_multi_header_variant_names_unique(bench_dir: Path) -> None:
    # multi_header 변형: 첫 행이 그룹 행이라 원본 헤더가 겹친다(orders_kr 7개 중복).
    result = run_pipeline(bench_dir / "multi_header.csv")
    sources = [column.name for column in result.ingest.columns]
    targets = [column.name for column in result.candidates[0].tables[0].columns]
    assert len(sources) == len(set(sources))
    assert len(targets) == len(set(targets))
    assert [mapping.source_column for mapping in result.load_plan.mappings] == sources


def test_pipeline_strips_utf8_bom(tmp_path: Path) -> None:
    csv_path = tmp_path / "bom.csv"
    csv_path.write_text("customer_id,amount\n1,100\n", encoding="utf-8-sig")
    result = run_pipeline(csv_path)
    assert result.ingest.columns[0].name == "customer_id"
    assert result.load_plan.mappings[0].source_column == "customer_id"


def test_cli_refuses_overwriting_input(bench_csv: Path) -> None:
    assert cli_main(["analyze", str(bench_csv), "--no-llm", "--out", str(bench_csv)]) == 2


def test_cli_refuses_hardlinked_out(bench_csv: Path, tmp_path: Path) -> None:
    # resolve() 비교로는 못 잡는 하드 링크: samefile(아이노드) 비교로 막는다.
    hardlink = tmp_path / "hardlink.csv"
    os.link(bench_csv, hardlink)
    assert cli_main(["analyze", str(bench_csv), "--no-llm", "--out", str(hardlink)]) == 2
    # 원본이 보고서로 덮어씌워지지 않았는지 확인(여전히 CSV 헤더).
    assert bench_csv.read_text(encoding="utf-8").startswith("customer_id")
