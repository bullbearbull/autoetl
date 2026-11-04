"""S0 수집·정규화(IngestManifest)와 S1 샘플링 전략(SamplingPlan) 계약."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, NonNegativeInt

from autoetl.contracts.common import CONTRACTS_VERSION, ContractModel


class IngestFormat(str, Enum):
    CSV = "csv"
    TSV = "tsv"
    XLSX = "xlsx"
    XLS = "xls"
    JSON = "json"
    PARQUET = "parquet"


class SourceFile(ContractModel):
    """정규화된 입력 파일 하나. 모든 값은 문자열로 로드된다."""

    path: str = Field(description="원본 파일 경로 또는 표준화된 표시명")
    format: IngestFormat
    encoding: str = Field(description="감지된 인코딩. 예: 'utf-8', 'cp949'")
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(
        default=None, description="재현성용 파일 해시. 업로드 스트림이면 생략"
    )
    sheet: str | None = Field(default=None, description="엑셀 시트 이름")


class SourceColumn(ContractModel):
    """정규화 후 컬럼 하나. 이름은 다중 행 헤더 해제 결과다."""

    name: str = Field(min_length=1)
    index: NonNegativeInt


class IngestManifest(ContractModel):
    """S0 산출물: 수집·정규화 결과 명세.

    인코딩 감지, 구분자·헤더 추론, 다중 행 헤더와 병합 셀 해제를 마친 뒤의 상태를
    기록한다. pandas 자동 형 변환으로 인한 정보 손실(앞자리 0, 지수 표기)을 막으려고
    모든 값을 문자열로 로드했음을 전제로 한다.
    """

    contract_version: str = CONTRACTS_VERSION
    files: list[SourceFile] = Field(min_length=1)
    columns: list[SourceColumn] = Field(min_length=1, description="정규화된 컬럼 순서")
    row_count: int = Field(ge=0, description="정규화 후 데이터 행 수")
    delimiter: str | None = Field(default=None, description="구분자. CSV 계열에서만")
    header_row_count: int = Field(default=1, ge=0, description="헤더로 소비한 행 수")
    applied_normalizations: list[str] = Field(
        default_factory=list,
        description="적용한 정규화 목록. 예: 'multi_row_header_flattened'",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="비치명 경고. 예: '줄 3에서 따옴표 불일치', '빈 컬럼명을 col_7로 명명'",
    )


class SamplingMethod(str, Enum):
    RANDOM = "random"
    STRATIFIED = "stratified"
    TEMPORAL_EVEN = "temporal_even"
    TOP_N = "top_n"


class Stratum(ContractModel):
    """층화 샘플의 층 하나."""

    column: str
    value: str
    weight: float = Field(ge=0, le=1, description="전체에서 이 층에 배정할 비율")


class SamplingPlan(ContractModel):
    """S1 산출물: 샘플링 전략.

    전수 판정이 나면 샘플 없이 통계를 DuckDB로 스트리밍한다. 샘플 판정이면 시드를
    고정해 재현성을 보장한다.
    """

    contract_version: str = CONTRACTS_VERSION
    strategy: str = Field(pattern="^(full|sample)$")
    method: SamplingMethod | None = Field(default=None, description="전수일 때는 없음")
    total_rows: int = Field(ge=0)
    sample_rows: int | None = Field(default=None, ge=1, description="샘플 크기. 전수일 때는 없음")
    seed: int | None = Field(default=None, description="재현용 시드")
    strata: list[Stratum] = Field(default_factory=list, description="층화 샘플의 층 정의")
    temporal_column: str | None = Field(default=None, description="시간 균등 샘플의 기준 컬럼")
    rationale: str = Field(description="전수/샘플을 고른 이유")
