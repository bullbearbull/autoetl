"""빈 파이프라인 러너 v0.

SOP S0~S14를 계약(T-0005)을 따라 순서대로 연결한다. 각 단계는 아직 실제 분석을
하지 않는 v0 스텁이다 — 계약의 최소 유효 값만 채워 파이프라인 전체가 벤치 v0
데이터셋 위에서 끝까지 도는지가 이 단계의 목적이다(P0 완료 기준). 실제 결정론
구현은 P1이, LLM 단계는 P2가 맡는다.

v0 범위: 입력은 UTF-8 CSV 1개, LLM 없음(--no-llm)만 지원.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from autoetl import __version__
from autoetl.contracts import (
    ColumnMapping,
    ColumnMeaning,
    ColumnProfile,
    ColumnStats,
    DeduplicationRule,
    DimensionScore,
    Evidence,
    IncrementMode,
    IngestFormat,
    IngestManifest,
    LengthStats,
    LoadOrder,
    LoadPlan,
    NullHandling,
    PhysicalType,
    Purpose,
    QualityReport,
    RankedCandidate,
    Recommendation,
    RelationGraph,
    Report,
    ReportSection,
    ReportSectionId,
    SamplingPlan,
    SchemaCandidate,
    SchemaPattern,
    Scorecard,
    SelectedSchema,
    SemanticModel,
    SemanticType,
    SensitivityMap,
    SourceColumn,
    SourceFile,
    StatsKind,
    TargetColumn,
    TargetTable,
    TypeAssignment,
    ValidationSummary,
)
from autoetl.contracts.schema import CriterionScore

SOP_VERSION = "v1"
"""sop/v1.yaml의 version과 같아야 한다(테스트가 대조한다)."""

STUB_NOTE = "v0 빈 구현"


def _read_csv(path: Path) -> tuple[list[str], int, int]:
    """UTF-8 CSV의 헤더·데이터 행 수·크기를 읽는 최소 S0 입력 파서.

    utf-8-sig로 읽어 엑셀이 붙이는 BOM을 떼어 낸다(BOM이 없으면 그대로).
    행 수는 스트리밍으로 세어 전체 파일을 메모리에 올리지 않는다.
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        first = next(reader, None)
        if first is None:
            raise ValueError(f"빈 CSV: {path}")
        header = [name.strip() or f"col_{i + 1}" for i, name in enumerate(first)]
        row_count = sum(1 for _ in reader)
    return header, row_count, path.stat().st_size


def _sanitize_names(header: list[str]) -> dict[str, str]:
    """원본 컬럼 이름 → 표준 영문 이름(^[a-z][a-z0-9_]*$) 매핑.

    ASCII가 아닌 글자(한글 헤더 등)는 버리고, 비면 col_N으로 채운다.
    충돌(예: 'foo-bar'와 'foo bar')은 숫자 접미로 유일하게 만든다.
    """
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for index, name in enumerate(header):
        cleaned = "".join(ch if ch.isascii() and ch.isalnum() else "_" for ch in name.lower())
        parts = [part for part in cleaned.split("_") if part]
        base = "_".join(parts)
        if not base or not base[0].isalpha():
            base = f"col_{index + 1}"
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f"{base}_{suffix}"
            suffix += 1
        taken.add(candidate)
        mapping[name] = candidate
    return mapping


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
    """S0~S14를 빈 구현으로 실행한다. LLM은 쓰지 않는다(--no-llm 전용)."""
    header, row_count, size_bytes = _read_csv(input_path)
    safe = _sanitize_names(header)
    file_name = input_path.name

    # S0 수집·정규화 — 실제 헤더와 행 수만 반영한다.
    ingest = IngestManifest(
        files=[
            SourceFile(
                path=file_name,
                format=IngestFormat.CSV,
                encoding="utf-8",
                size_bytes=size_bytes,
            )
        ],
        columns=[SourceColumn(name=name, index=i) for i, name in enumerate(header)],
        row_count=row_count,
        delimiter=",",
    )

    # S1 샘플링 — v0은 항상 전수.
    sampling = SamplingPlan(
        strategy="full",
        total_rows=row_count,
        rationale=f"{STUB_NOTE}: 전수 고정",
    )

    # S2 구조 프로파일 — 최소 통계(0)만.
    profiles = [
        ColumnProfile(
            column=name,
            count=0,
            missing_count=0,
            missing_rate=0.0,
            unique_count=0,
            cardinality_ratio=0.0,
            length=LengthStats(minimum=0, maximum=0, mean=0.0, p50=0.0, p95=0.0),
        )
        for name in header
    ]

    # S3 타입 추론 — 전부 STRING/FREE_TEXT 스텁 판정.
    types = [
        TypeAssignment(
            column=name,
            physical_type=PhysicalType.STRING,
            semantic_type=SemanticType.FREE_TEXT,
            detector="stub.v0",
            confidence=0.0,
        )
        for name in header
    ]

    # S4 심화 통계 — 종류만 OTHER로 등록.
    stats = [ColumnStats(column=name, kind=StatsKind.OTHER) for name in header]

    # S5 관계 분석 — 빈 그래프.
    relations = RelationGraph()

    # S6 품질 진단 — 0점 스텁.
    quality = QualityReport(
        completeness=DimensionScore(score=0.0, detail=STUB_NOTE),
        validity=DimensionScore(score=0.0, detail=STUB_NOTE),
        consistency=DimensionScore(score=0.0, detail=STUB_NOTE),
        uniqueness=DimensionScore(score=0.0, detail=STUB_NOTE),
        duplicate_row_count=0,
    )

    # S7 민감도 — 민감 없음 스텁.
    sensitivity = SensitivityMap()

    # S8 의미 해석 — 첫 컬럼 하나에 미확정 의미만 남긴다(LLM 없음).
    semantic = SemanticModel(
        business_domain=f"{STUB_NOTE}: 미확정",
        column_meanings=[
            ColumnMeaning(
                column=header[0],
                meaning=f"{STUB_NOTE}: 미확정",
                standard_name=safe[header[0]],
                evidence=[Evidence(ref="IngestManifest:columns", reason=STUB_NOTE)],
            )
        ],
        unresolved_columns=header[1:],
        evidence=[Evidence(ref="IngestManifest", reason=STUB_NOTE)],
    )

    # S9 후보 스키마 — 전체 컬럼을 담은 단일 와이드 후보.
    wide_table = TargetTable(
        name="wide_table",
        columns=[
            TargetColumn(
                name=safe[name],
                physical_type=PhysicalType.STRING,
                source_columns=[name],
                nullable=True,
                is_primary_key=(i == 0),
            )
            for i, name in enumerate(header)
        ],
    )
    candidates = [
        SchemaCandidate(
            id="C1",
            pattern=SchemaPattern.WIDE,
            tables=[wide_table],
            rationale=f"{STUB_NOTE}: 단일 와이드 후보",
        )
    ]

    # S10 검증·채점 — 미실행 0점.
    def _zero_criterion() -> CriterionScore:
        return CriterionScore(score=0.0, detail=STUB_NOTE)

    scorecards = [
        Scorecard(
            candidate_id=candidate.id,
            integrity=_zero_criterion(),
            redundancy=_zero_criterion(),
            fitness=_zero_criterion(),
            load_simplicity=_zero_criterion(),
            storage_efficiency=_zero_criterion(),
            extensibility=_zero_criterion(),
            security_isolation=_zero_criterion(),
            weighted_total=0.0,
            purpose=purpose,
            validation=ValidationSummary(
                ddl_parse_ok=False,
                trial_load_ok=False,
                constraint_violation_count=0,
                data_loss_rate=0.0,
            ),
        )
        for candidate in candidates
    ]

    # S11 권고 — 단일 후보 자동 추천.
    recommendation = Recommendation(
        ranking=[
            RankedCandidate(
                candidate_id="C1",
                rank=1,
                reasons=[Evidence(ref="Scorecard:C1", reason=STUB_NOTE)],
            )
        ],
        recommended_id="C1",
        tradeoffs=f"{STUB_NOTE}: 비교 대상 없음",
    )

    # S12 선택 — 비대화 모드 규칙대로 추천안 자동 선택.
    selected = SelectedSchema(
        candidate_id="C1",
        purpose=purpose,
        revalidated=False,
        selection_basis=f"{STUB_NOTE}: 단일 후보 자동 선택",
    )

    # S13 적재 플랜 — 1:1 매핑, append.
    load_plan = LoadPlan(
        mappings=[
            ColumnMapping(
                source_column=name,
                target_table="wide_table",
                target_column=safe[name],
                type_cast="text",
                null_handling=NullHandling.NULL,
            )
            for name in header
        ],
        load_order=[LoadOrder(table="wide_table")],
        deduplication=DeduplicationRule(enabled=False, keys=[]),
        increment=IncrementMode.APPEND,
        expected_row_count=row_count,
    )

    # S14 보고서 — 표준 목차 11절.
    report = Report(
        generated_at=datetime.now(timezone.utc).isoformat(),
        pipeline_version=__version__,
        sop_version=SOP_VERSION,
        purpose=purpose.value,
        sections=[
            ReportSection(id=section_id, title=section_id.value) for section_id in ReportSectionId
        ],
        reproduction={"rows": str(row_count), "columns": str(len(header))},
    )

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
