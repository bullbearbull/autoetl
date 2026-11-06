"""S1 샘플링 검증(T-0024) — 전수 한도·시드 표본·재현성·rationale."""

from __future__ import annotations

from pathlib import Path

from bench.generator import PACKS, build_dataset

from autoetl.core.context import AnalysisContext
from autoetl.core.pipeline import run_pipeline
from autoetl.core.stages.s01_sampling import (
    DEFAULT_SEED,
    FULL_SCAN_ROW_LIMIT,
    SAMPLE_RELATION,
    SAMPLE_RELATION_KEY,
    run,
)


def _numeric_csv(path: Path, rows: int) -> Path:
    lines = ["id,value,label"]
    lines += [f"{i},value-{i:04d},라벨{i}" for i in range(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_at_limit_is_full_scan(tmp_path: Path) -> None:
    # 한도와 같아도(초과가 아니어도) 전수다. 전수면 시드·표본 크기가 없다.
    path = _numeric_csv(tmp_path / "small.csv", 10)
    with AnalysisContext(path) as ctx:
        plan = run(ctx, full_scan_limit=10)
    assert plan.strategy == "full"
    assert plan.total_rows == 10
    assert plan.method is None
    assert plan.sample_rows is None
    assert plan.seed is None
    assert "전수" in plan.rationale


def test_over_limit_samples_with_seed_and_rationale(tmp_path: Path) -> None:
    # 완료 기준: 표본 전략·시드·rationale이 기록된다(층화·시간 균등 미실시 사유 포함).
    path = _numeric_csv(tmp_path / "big.csv", 30)
    with AnalysisContext(path) as ctx:
        plan = run(ctx, full_scan_limit=5, sample_limit=10, seed=123)
        assert plan.strategy == "sample"
        assert plan.method is not None and plan.method.value == "random"
        assert plan.total_rows == 30
        assert plan.sample_rows == 10
        assert plan.seed == 123
        assert "층화·시간 균등" in plan.rationale and "S3" in plan.rationale
        # 표본 테이블이 만들어져 stage_artifacts에 등록된다.
        assert ctx.stage_artifacts[SAMPLE_RELATION_KEY] == SAMPLE_RELATION
        sampled = ctx.connection.execute(f"SELECT COUNT(*) FROM {SAMPLE_RELATION}").fetchone()
        assert sampled is not None and sampled[0] == 10


def test_sample_rows_capped_at_total(tmp_path: Path) -> None:
    # 표본 크기 기본값이 전체 행 수보다 크면 전체 행 수로 줄인다.
    path = _numeric_csv(tmp_path / "tiny.csv", 8)
    with AnalysisContext(path) as ctx:
        plan = run(ctx, full_scan_limit=4, sample_limit=100)
    assert plan.sample_rows == 8


def test_same_seed_yields_same_sample(tmp_path: Path) -> None:
    # 완료 기준: 같은 시드는 같은 표본을 낸다(두 컨텍스트에서 재실행해도).
    path = _numeric_csv(tmp_path / "sampled.csv", 40)

    def sample_rows(seed: int) -> list[tuple[str, ...]]:
        with AnalysisContext(path) as ctx:
            run(ctx, full_scan_limit=5, sample_limit=12, seed=seed)
            # 표본 테이블도 내부 식별자(c0, c1, …)를 쓴다.
            rows = ctx.connection.execute(
                f'SELECT "c0", "c1", "c2" FROM {SAMPLE_RELATION} ORDER BY "c0"'
            ).fetchall()
        return rows

    first = sample_rows(DEFAULT_SEED)
    second = sample_rows(DEFAULT_SEED)
    other = sample_rows(DEFAULT_SEED + 1)
    assert len(first) == 12
    assert first == second
    assert first != other  # 시드가 다르면 표본이 다르다


def test_pipeline_defaults_to_full_scan_for_bench(tmp_path: Path) -> None:
    # 기본 전수 한도(1,000,000행)에서는 벤치 데이터셋이 전수로 검사된다.
    out = tmp_path / "orders-42"
    build_dataset(PACKS["orders_kr"], seed=42, out_dir=out)
    result = run_pipeline(out / "clean.csv")
    assert result.sampling.strategy == "full"
    assert result.sampling.total_rows == 900 <= FULL_SCAN_ROW_LIMIT
    # 전수면 표본 테이블을 만들지 않는다.
    with AnalysisContext(out / "clean.csv") as ctx:
        run(ctx)
        assert SAMPLE_RELATION_KEY not in ctx.stage_artifacts
