"""S9 후보 스키마 설계 — SchemaCandidate 목록."""

from __future__ import annotations

from autoetl.contracts import (
    IngestManifest,
    PhysicalType,
    SchemaCandidate,
    SchemaPattern,
    TargetColumn,
    TargetTable,
)
from autoetl.core.context import AnalysisContext
from autoetl.core.stages import STUB_NOTE
from autoetl.core.stages.s00_ingest import sanitize_names, source_names


def run(ctx: AnalysisContext, ingest: IngestManifest) -> list[SchemaCandidate]:
    """전체 컬럼을 담은 단일 와이드 후보."""
    sources = source_names(ingest)
    safe = sanitize_names(sources)
    wide_table = TargetTable(
        name="wide_table",
        columns=[
            TargetColumn(
                name=safe[i],
                physical_type=PhysicalType.STRING,
                source_columns=[source],
                nullable=True,
                is_primary_key=(i == 0),
            )
            for i, source in enumerate(sources)
        ],
    )
    return [
        SchemaCandidate(
            id="C1",
            pattern=SchemaPattern.WIDE,
            tables=[wide_table],
            rationale=f"{STUB_NOTE}: 단일 와이드 후보",
        )
    ]
