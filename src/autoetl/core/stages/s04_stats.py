"""S4 심화 통계 — ColumnStats 목록."""

from __future__ import annotations

from autoetl.contracts import ColumnStats, IngestManifest, StatsKind
from autoetl.core.context import AnalysisContext
from autoetl.core.stages.s00_ingest import source_names


def run(ctx: AnalysisContext, ingest: IngestManifest) -> list[ColumnStats]:
    """종류만 OTHER로 등록."""
    return [ColumnStats(column=name, kind=StatsKind.OTHER) for name in source_names(ingest)]
