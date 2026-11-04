"""S14 보고서(Report) 계약."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from autoetl.contracts.common import CONTRACTS_VERSION, ContractModel


class ReportSectionId(str, Enum):
    """보고서 표준 목차(docs/PLAN.md)."""

    SUMMARY = "summary"
    DATASET_OVERVIEW = "dataset_overview"
    COLUMN_PROFILES = "column_profiles"
    FEATURE_CLASSIFICATION = "feature_classification"
    RELATIONS = "relations"
    PATTERNS = "patterns"
    QUALITY = "quality"
    SENSITIVITY = "sensitivity"
    SCHEMA_COMPARISON = "schema_comparison"
    LOAD_PLAN = "load_plan"
    APPENDIX = "appendix"


class ReportSection(ContractModel):
    id: ReportSectionId
    title: str
    artifact_refs: list[str] = Field(
        default_factory=list,
        description="이 절을 채운 산출물 ID. 예: 'ColumnProfile:phone', 'Q-0003'",
    )


class Report(ContractModel):
    """S14 산출물: 보고서.

    JSON 산출물을 템플릿(Jinja2)으로 렌더링해 HTML/PDF/Markdown으로 낸다.
    부록에는 분석 파라미터, SOP·프롬프트·모델 버전, 재현 정보가 들어간다.
    """

    contract_version: str = CONTRACTS_VERSION
    generated_at: str = Field(description="ISO 8601")
    pipeline_version: str = Field(description="autoetl 버전")
    sop_version: str = Field(default="", description="적용한 SOP 버전. 예: 'v1'")
    purpose: str = Field(default="", description="분석 때 고른 용도")
    sections: list[ReportSection] = Field(min_length=1)
    reproduction: dict[str, str] = Field(
        default_factory=dict,
        description="재현 정보. 예: {'input_sha256': '...', 'seed': '42'}",
    )
