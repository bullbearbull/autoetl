"""S8 의미 해석 — SemanticModel."""

from __future__ import annotations

from autoetl.contracts import ColumnMeaning, Evidence, IngestManifest, SemanticModel
from autoetl.core.context import AnalysisContext
from autoetl.core.stages import STUB_NOTE
from autoetl.core.stages.s00_ingest import sanitize_names, source_names


def run(ctx: AnalysisContext, ingest: IngestManifest) -> SemanticModel:
    """첫 컬럼 하나에 미확정 의미만 남긴다(LLM 없음)."""
    sources = source_names(ingest)
    safe = sanitize_names(sources)
    return SemanticModel(
        business_domain=f"{STUB_NOTE}: 미확정",
        column_meanings=[
            ColumnMeaning(
                column=sources[0],
                meaning=f"{STUB_NOTE}: 미확정",
                standard_name=safe[0],
                evidence=[Evidence(ref="IngestManifest:columns", reason=STUB_NOTE)],
            )
        ],
        unresolved_columns=sources[1:],
        evidence=[Evidence(ref="IngestManifest", reason=STUB_NOTE)],
    )
