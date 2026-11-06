"""파이프라인 러너 — S0~S14를 순서대로 잇는다.

각 단계의 실제 구현은 core/stages/ 아래 단계 모듈이고(규약은 core/README.md),
이 파일은 AnalysisContext 생성과 순서 호출·결과 조립만 한다. v0 범위: 입력은
CSV 1개(인코딩·구분자·헤더 행은 S0가 감지), LLM 없음(--no-llm)만 지원.
실제 결정론 구현은 P1이, LLM 단계는 P2가 맡는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autoetl.contracts import (
    ColumnProfile,
    ColumnStats,
    IngestManifest,
    LoadPlan,
    Purpose,
    QualityReport,
    Recommendation,
    RelationGraph,
    Report,
    SamplingPlan,
    SchemaCandidate,
    Scorecard,
    SelectedSchema,
    SemanticModel,
    SensitivityMap,
    TypeAssignment,
)
from autoetl.core.context import AnalysisContext
from autoetl.core.errors import InputError
from autoetl.core.stages import (
    SOP_VERSION,
    s00_ingest,
    s01_sampling,
    s02_profile,
    s03_types,
    s04_stats,
    s05_relations,
    s06_quality,
    s07_sensitivity,
    s08_semantic,
    s09_candidates,
    s10_scorecards,
    s11_recommendation,
    s12_selection,
    s13_load_plan,
    s14_report,
)

__all__ = ["SOP_VERSION", "InputError", "PipelineResult", "run_pipeline"]


@dataclass(frozen=True)
class PipelineResult:
    """한 번 실행의 전체 산출물. 각 필드는 T-0005 계약 인스턴스다."""

    ingest: IngestManifest
    sampling: SamplingPlan
    profiles: list[ColumnProfile]
    types: list[TypeAssignment]
    stats: list[ColumnStats]
    relations: RelationGraph
    quality: QualityReport
    sensitivity: SensitivityMap
    semantic: SemanticModel
    candidates: list[SchemaCandidate]
    scorecards: list[Scorecard]
    recommendation: Recommendation
    selected: SelectedSchema
    load_plan: LoadPlan
    report: Report


def run_pipeline(
    input_path: Path,
    *,
    purpose: Purpose = Purpose.ANALYTICS,
) -> PipelineResult:
    """S0~S14를 실행한다. LLM은 쓰지 않는다(--no-llm 전용)."""
    spec = s00_ingest.probe_source(input_path)
    with AnalysisContext(input_path, spec=spec) as ctx:
        ingest = s00_ingest.run(ctx, spec)
        ctx.stage_artifacts["s00_ingest"] = ingest
        sampling = s01_sampling.run(ctx)
        ctx.stage_artifacts["s01_sampling"] = sampling
        profiles = s02_profile.run(ctx, ingest, sampling)
        ctx.stage_artifacts["s02_profile"] = profiles
        types = s03_types.run(ctx, ingest, profiles)
        ctx.stage_artifacts["s03_types"] = types
        stats = s04_stats.run(ctx, ingest)
        ctx.stage_artifacts["s04_stats"] = stats
        relations = s05_relations.run(ctx)
        ctx.stage_artifacts["s05_relations"] = relations
        quality = s06_quality.run(ctx)
        ctx.stage_artifacts["s06_quality"] = quality
        sensitivity = s07_sensitivity.run(ctx)
        ctx.stage_artifacts["s07_sensitivity"] = sensitivity
        semantic = s08_semantic.run(ctx, ingest)
        ctx.stage_artifacts["s08_semantic"] = semantic
        candidates = s09_candidates.run(ctx, ingest)
        ctx.stage_artifacts["s09_candidates"] = candidates
        scorecards = s10_scorecards.run(ctx, candidates, purpose=purpose)
        ctx.stage_artifacts["s10_scorecards"] = scorecards
        recommendation = s11_recommendation.run(ctx, candidates)
        ctx.stage_artifacts["s11_recommendation"] = recommendation
        selected = s12_selection.run(ctx, recommendation, purpose=purpose)
        ctx.stage_artifacts["s12_selection"] = selected
        load_plan = s13_load_plan.run(ctx, ingest)
        ctx.stage_artifacts["s13_load_plan"] = load_plan
        report = s14_report.run(ctx, purpose=purpose)
        ctx.stage_artifacts["s14_report"] = report

    return PipelineResult(
        ingest=ingest,
        sampling=sampling,
        profiles=profiles,
        types=types,
        stats=stats,
        relations=relations,
        quality=quality,
        sensitivity=sensitivity,
        semantic=semantic,
        candidates=candidates,
        scorecards=scorecards,
        recommendation=recommendation,
        selected=selected,
        load_plan=load_plan,
        report=report,
    )
