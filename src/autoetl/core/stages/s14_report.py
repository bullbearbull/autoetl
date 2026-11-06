"""S14 보고서 — Report."""

from __future__ import annotations

from datetime import datetime, timezone

from autoetl import __version__
from autoetl.contracts import Purpose, Report, ReportSection, ReportSectionId
from autoetl.core.context import AnalysisContext
from autoetl.core.stages import SOP_VERSION


def run(ctx: AnalysisContext, *, purpose: Purpose) -> Report:
    """표준 목차 11절 보고서."""
    return Report(
        generated_at=datetime.now(timezone.utc).isoformat(),
        pipeline_version=__version__,
        sop_version=SOP_VERSION,
        purpose=purpose.value,
        sections=[
            ReportSection(id=section_id, title=section_id.value) for section_id in ReportSectionId
        ],
        reproduction={"rows": str(ctx.row_count), "columns": str(len(ctx.header))},
    )
