"""S13 적재 플랜 — LoadPlan."""

from __future__ import annotations

from autoetl.contracts import (
    ColumnMapping,
    DeduplicationRule,
    IncrementMode,
    IngestManifest,
    LoadOrder,
    LoadPlan,
    NullHandling,
)
from autoetl.core.context import AnalysisContext
from autoetl.core.stages.s00_ingest import sanitize_names, source_names


def run(ctx: AnalysisContext, ingest: IngestManifest) -> LoadPlan:
    """1:1 매핑, append 스텁."""
    sources = source_names(ingest)
    safe = sanitize_names(sources)
    return LoadPlan(
        mappings=[
            ColumnMapping(
                source_column=source,
                target_table="wide_table",
                target_column=safe[i],
                type_cast="text",
                null_handling=NullHandling.NULL,
            )
            for i, source in enumerate(sources)
        ],
        load_order=[LoadOrder(table="wide_table")],
        deduplication=DeduplicationRule(enabled=False, keys=[]),
        increment=IncrementMode.APPEND,
        expected_row_count=ctx.row_count,
    )
