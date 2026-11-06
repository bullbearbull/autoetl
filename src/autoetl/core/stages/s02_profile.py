"""S2 구조 프로파일 — ColumnProfile 목록."""

from __future__ import annotations

from autoetl.contracts import ColumnProfile, IngestManifest, LengthStats
from autoetl.core.context import AnalysisContext
from autoetl.core.stages.s00_ingest import source_names


def run(ctx: AnalysisContext, ingest: IngestManifest) -> list[ColumnProfile]:
    """최소 통계(0)만."""
    return [
        ColumnProfile(
            column=name,
            count=0,
            missing_count=0,
            missing_rate=0.0,
            unique_count=0,
            cardinality_ratio=0.0,
            length=LengthStats(minimum=0, maximum=0, mean=0.0, p50=0.0, p95=0.0),
        )
        for name in source_names(ingest)
    ]
