"""S3 타입 추론 — TypeAssignment 목록."""

from __future__ import annotations

from autoetl.contracts import (
    IngestManifest,
    PhysicalType,
    SemanticType,
    TypeAssignment,
)
from autoetl.core.context import AnalysisContext
from autoetl.core.stages.s00_ingest import source_names


def run(ctx: AnalysisContext, ingest: IngestManifest) -> list[TypeAssignment]:
    """전부 STRING/FREE_TEXT 스텁 판정. 실제 추론은 이 단계 과업이 넣는다."""
    return [
        TypeAssignment(
            column=name,
            physical_type=PhysicalType.STRING,
            semantic_type=SemanticType.FREE_TEXT,
            detector="stub.v0",
            confidence=0.0,
        )
        for name in source_names(ingest)
    ]
