"""단계별 계약 v0 검증."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoetl.contracts import (
    ARTIFACTS,
    CONTRACTS_VERSION,
    AllowedSchemaOp,
    CandidateKey,
    CategoricalStats,
    ColumnMapping,
    ColumnMeaning,
    ColumnProfile,
    ColumnStats,
    Correlation,
    CorrelationMethod,
    CriterionScore,
    DeduplicationRule,
    Derivation,
    DimensionScore,
    EntityGroup,
    Evidence,
    FunctionalDependency,
    InclusionDependency,
    IncrementMode,
    IngestFormat,
    IngestManifest,
    LengthStats,
    LoadOrder,
    LoadPlan,
    NullHandling,
    NumericStats,
    OutlierMethod,
    OutlierSummary,
    PatternMask,
    PhysicalType,
    Purpose,
    QualityDimension,
    QualityIssue,
    QualityReport,
    RankedCandidate,
    Recommendation,
    RelationGraph,
    Report,
    ReportSection,
    ReportSectionId,
    SamplingMethod,
    SamplingPlan,
    SchemaCandidate,
    SchemaModification,
    SchemaPattern,
    Scorecard,
    SelectedSchema,
    SemanticModel,
    SemanticType,
    SensitiveType,
    SensitivityAssignment,
    SensitivityLevel,
    SensitivityMap,
    Severity,
    SourceColumn,
    SourceFile,
    StatsKind,
    Stratum,
    TargetColumn,
    TargetTable,
    TypeAlternative,
    TypeAssignment,
    ValidationSummary,
)

_SPEC = importlib.util.spec_from_file_location(
    "export_contracts", Path(__file__).resolve().parents[1] / "tools" / "export_contracts.py"
)
assert _SPEC is not None and _SPEC.loader is not None
export_contracts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(export_contracts)


def _ingest_manifest() -> IngestManifest:
    return IngestManifest(
        files=[
            SourceFile(path="sales.csv", format=IngestFormat.CSV, encoding="cp949", size_bytes=1024)
        ],
        columns=[SourceColumn(name="주문번호", index=0), SourceColumn(name="금액", index=1)],
        row_count=100,
        delimiter=",",
        applied_normalizations=["multi_row_header_flattened"],
        warnings=[],
    )


def _sampling_plan() -> SamplingPlan:
    return SamplingPlan(
        strategy="sample",
        method=SamplingMethod.STRATIFIED,
        total_rows=10000,
        sample_rows=1000,
        seed=42,
        strata=[Stratum(column="지역", value="서울", weight=0.4)],
        rationale="개발 속도 우선, 재현 시드 고정",
    )


def _column_profile() -> ColumnProfile:
    return ColumnProfile(
        column="주문번호",
        count=98,
        missing_count=2,
        missing_rate=0.02,
        unique_count=97,
        cardinality_ratio=0.989,
        length=LengthStats(minimum=8, maximum=10, mean=9.1, p50=9.0, p95=10.0),
        top_patterns=[PatternMask(mask="a9999999", count=80, rate=0.816)],
    )


def _type_assignment() -> TypeAssignment:
    return TypeAssignment(
        column="금액",
        physical_type=PhysicalType.DECIMAL,
        semantic_type=SemanticType.AMOUNT,
        precision=12,
        scale=2,
        detector="numeric.precision",
        confidence=0.92,
        alternatives=[
            TypeAlternative(semantic_type=SemanticType.CONTINUOUS_NUMBER, confidence=0.3)
        ],
        notes=["천 단위 콤마 제거 필요"],
    )


def _column_stats() -> ColumnStats:
    return ColumnStats(
        column="금액",
        kind=StatsKind.NUMERIC,
        numeric=NumericStats(
            mean=52300.5,
            std=12000.0,
            minimum=1000.0,
            maximum=99000.0,
            p25=45000.0,
            p50=51000.0,
            p75=60000.0,
            skew=0.6,
            max_decimal_digits=2,
            outliers=OutlierSummary(
                method=OutlierMethod.IQR, count=3, rate=0.03, bounds="IQR 1.5x: [1000, 99000]"
            ),
        ),
        categorical=CategoricalStats(entropy=3.2, top_values=[], distinct_ratio=0.8),
    )


def _relation_graph() -> RelationGraph:
    return RelationGraph(
        candidate_keys=[CandidateKey(columns=["주문번호"], unique=True, violation_count=0)],
        functional_dependencies=[
            FunctionalDependency(lhs=["주문번호"], rhs="주문일시", support=1.0, violation_count=0)
        ],
        inclusion_dependencies=[
            InclusionDependency(
                child_columns=["고객ID"], parent_columns=["고객ID"], inclusion_rate=0.98
            )
        ],
        correlations=[
            Correlation(a="금액", b="수량", method=CorrelationMethod.PEARSON, value=0.82)
        ],
        derivations=[
            Derivation(target="금액", sources=["단가", "수량"], rule_hint="금액 = 단가 x 수량")
        ],
    )


def _quality_report() -> QualityReport:
    return QualityReport(
        completeness=DimensionScore(score=98.0, detail="결측 2%"),
        validity=DimensionScore(score=95.0, detail="형식 위반 12건"),
        consistency=DimensionScore(score=99.0, detail="단위 혼재 없음"),
        uniqueness=DimensionScore(score=97.0, detail="중복 행 3건"),
        duplicate_row_count=3,
        issues=[
            QualityIssue(
                id="Q-0001",
                dimension=QualityDimension.VALIDITY,
                severity=Severity.WARNING,
                columns=["이메일"],
                message="이메일 형식 불일치",
                count=12,
                rate=0.012,
            )
        ],
    )


def _sensitivity_map() -> SensitivityMap:
    return SensitivityMap(
        assignments=[
            SensitivityAssignment(
                column="연락처",
                types=[SensitiveType.PHONE_NUMBER],
                level=SensitivityLevel.L2,
                detector="regex.phone",
                confidence=0.99,
                matched_rate=0.95,
            )
        ]
    )


def _semantic_model() -> SemanticModel:
    return SemanticModel(
        business_domain="전자상거래 주문",
        column_meanings=[
            ColumnMeaning(
                column="주문번호",
                meaning="주문 식별자",
                standard_name="order_id",
                evidence=[Evidence(ref="ColumnProfile:주문번호", reason="고유값 비율 0.989")],
            )
        ],
        entities=[
            EntityGroup(
                name="customer",
                columns=["고객ID", "고객명"],
                rationale="고객ID가 나머지 컬럼을 결정",
                evidence=[Evidence(ref="RelationGraph:fd", reason="FD lhs=[고객ID]")],
            )
        ],
        evidence=[Evidence(ref="RelationGraph", reason="도메인 추정 근거")],
    )


def _schema_candidate() -> SchemaCandidate:
    return SchemaCandidate(
        id="C1",
        pattern=SchemaPattern.THIRD_NORMAL_FORM,
        tables=[
            TargetTable(
                name="orders",
                columns=[
                    TargetColumn(
                        name="order_id",
                        physical_type=PhysicalType.STRING,
                        source_columns=["주문번호"],
                        nullable=False,
                        is_primary_key=True,
                    ),
                    TargetColumn(
                        name="customer_id",
                        physical_type=PhysicalType.STRING,
                        source_columns=["고객ID"],
                        nullable=False,
                        foreign_key="customers(customer_id)",
                    ),
                ],
            )
        ],
        rationale="FD 기반 정규화",
    )


def _scorecard() -> Scorecard:
    return Scorecard(
        candidate_id="C1",
        integrity=CriterionScore(score=95.0, detail="무손실 조인"),
        redundancy=CriterionScore(score=90.0, detail="중복 낮음"),
        fitness=CriterionScore(score=88.0, detail="분석 용도 적합"),
        load_simplicity=CriterionScore(score=80.0, detail="FK 순서 2단계"),
        storage_efficiency=CriterionScore(score=85.0, detail="varchar 절약"),
        extensibility=CriterionScore(score=90.0, detail="확장 여유"),
        security_isolation=CriterionScore(score=70.0, detail="민감 미분리"),
        weighted_total=87.0,
        purpose=Purpose.ANALYTICS,
        validation=ValidationSummary(
            ddl_parse_ok=True, trial_load_ok=True, constraint_violation_count=0, data_loss_rate=0.0
        ),
    )


def _recommendation() -> Recommendation:
    return Recommendation(
        ranking=[
            RankedCandidate(
                candidate_id="C1",
                rank=1,
                reasons=[Evidence(ref="Scorecard:C1.integrity", reason="무결성 95점")],
            )
        ],
        recommended_id="C1",
        tradeoffs="C1은 적재가 다단계, wide는 중복이 큼",
        caution="민감 컬럼 격리 점수를 확인",
    )


def _selected_schema() -> SelectedSchema:
    return SelectedSchema(
        candidate_id="C1",
        purpose=Purpose.ANALYTICS,
        modifications=[
            SchemaModification(
                op=AllowedSchemaOp.RENAME, target="orders.order_id", args={"to": "id"}
            )
        ],
        revalidated=True,
        selection_basis="분석 용도에 적합",
    )


def _load_plan() -> LoadPlan:
    return LoadPlan(
        mappings=[
            ColumnMapping(
                source_column="주문번호",
                target_table="orders",
                target_column="order_id",
                type_cast="text",
                null_handling=NullHandling.REJECT_ROW,
            )
        ],
        load_order=[LoadOrder(table="orders")],
        deduplication=DeduplicationRule(enabled=True, keys=["주문번호"], keep="first"),
        increment=IncrementMode.UPSERT,
        upsert_keys=["주문번호"],
        expected_row_count=98,
    )


def _report() -> Report:
    return Report(
        generated_at="2026-11-05T00:00:00+09:00",
        pipeline_version="0.0.1",
        purpose="analytics",
        sections=[
            ReportSection(id=ReportSectionId.SUMMARY, title="요약", artifact_refs=["Q-0001"])
        ],
        reproduction={"seed": "42"},
    )


EXAMPLES = {
    "ingest_manifest": _ingest_manifest(),
    "sampling_plan": _sampling_plan(),
    "column_profile": _column_profile(),
    "type_assignment": _type_assignment(),
    "column_stats": _column_stats(),
    "relation_graph": _relation_graph(),
    "quality_report": _quality_report(),
    "sensitivity_map": _sensitivity_map(),
    "semantic_model": _semantic_model(),
    "schema_candidate": _schema_candidate(),
    "scorecard": _scorecard(),
    "recommendation": _recommendation(),
    "selected_schema": _selected_schema(),
    "load_plan": _load_plan(),
    "report": _report(),
}


def test_artifacts_registry_covers_examples() -> None:
    assert set(ARTIFACTS) == set(EXAMPLES)


def test_examples_round_trip() -> None:
    for name, example in EXAMPLES.items():
        round_trip = type(example).model_validate_json(example.model_dump_json())
        assert round_trip == example, name


def test_contract_version_recorded() -> None:
    for name, example in EXAMPLES.items():
        field = type(example).model_fields["contract_version"]
        assert field.default == CONTRACTS_VERSION, name


def test_extra_fields_forbidden() -> None:
    payload = _column_profile().model_dump()
    payload["bogus"] = 1
    with pytest.raises(ValidationError):
        ColumnProfile.model_validate(payload)


def test_llm_artifacts_require_evidence() -> None:
    data = _semantic_model().model_dump()
    data["evidence"] = []
    with pytest.raises(ValidationError):
        SemanticModel.model_validate(data)
    rec = _recommendation().model_dump()
    rec["ranking"][0]["reasons"] = []
    with pytest.raises(ValidationError):
        Recommendation.model_validate(rec)


def test_standard_name_pattern_enforced() -> None:
    data = _semantic_model().model_dump()
    data["column_meanings"][0]["standard_name"] = "Order-Id"
    with pytest.raises(ValidationError):
        SemanticModel.model_validate(data)


def test_exported_schemas_are_fresh() -> None:
    schemas = export_contracts.build_schemas()
    out_dir = Path(__file__).resolve().parents[1] / "schemas"
    committed = {path.name.removesuffix(".schema.json") for path in out_dir.glob("*.schema.json")}
    assert committed == set(schemas)
    for name, schema in schemas.items():
        on_disk = json.loads((out_dir / f"{name}.schema.json").read_text(encoding="utf-8"))
        assert on_disk == schema, name
        assert on_disk["$id"].endswith(f"/{name}.schema.json")
