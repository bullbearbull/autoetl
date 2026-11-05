"""빈 파이프라인 러너 v0 — 벤치 v0 위에서의 종주 검증."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from bench.generator import PACKS, build_dataset

from autoetl.cli import main as cli_main
from autoetl.contracts import ARTIFACTS, Report, ReportSectionId
from autoetl.core.pipeline import SOP_VERSION, run_pipeline


@pytest.fixture(scope="module")
def bench_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pack = PACKS["orders_kr"]
    out = tmp_path_factory.mktemp("bench") / f"{pack.name}-42"
    build_dataset(pack, seed=42, out_dir=out)
    return out


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
    with pytest.raises(ValueError, match="빈 CSV"):
        run_pipeline(empty)
