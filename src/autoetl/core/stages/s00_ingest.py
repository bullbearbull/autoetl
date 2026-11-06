"""S0 수집·정규화 — IngestManifest."""

from __future__ import annotations

from autoetl.contracts import IngestFormat, IngestManifest, SourceColumn, SourceFile
from autoetl.core.context import AnalysisContext


def deduplicate_source_names(header: list[str]) -> list[str]:
    """겹치는 원본 컬럼 이름에 발생 순서 접미(#N)를 붙여 유일하게 만든다.

    CSV 헤더는 같은 이름을 여러 개 가질 수 있고(벤치 v0의 multi_header 변형이
    그렇다), 그대로 두면 LoadPlan.source_column 같은 이름 참조가 둘을 구분하지
    못한다. 첫 등장은 원본 이름을 유지하고 두 번째부터 '#2', '#3'…를 붙인다.
    접미를 붙인 이름조차 실제 헤더와 겹치면 다음 접미로 밀어 유일성을 지킨다.
    """
    names: list[str] = []
    taken: set[str] = set()
    occurrences: dict[str, int] = {}
    for name in header:
        count = occurrences.get(name, 0) + 1
        occurrences[name] = count
        candidate = name if count == 1 else f"{name}#{count}"
        while candidate in taken:
            count += 1
            candidate = f"{name}#{count}"
        taken.add(candidate)
        names.append(candidate)
    return names


def sanitize_names(names: list[str]) -> list[str]:
    """표준 영문 이름(^[a-z][a-z0-9_]*$) 목록. 입력과 같은 길이·순서를 지킨다.

    ASCII가 아닌 글자(한글 헤더 등)는 버리고, 비면 col_N으로 채운다.
    충돌(예: 'foo-bar'와 'foo bar')은 숫자 접미로 유일하게 만든다.
    위치(인덱스) 기반으로 만들기 때문에 원본 이름이 겹쳐도 대상 이름은 유일하다.
    """
    safe: list[str] = []
    taken: set[str] = set()
    for index, name in enumerate(names):
        cleaned = "".join(ch if ch.isascii() and ch.isalnum() else "_" for ch in name.lower())
        parts = [part for part in cleaned.split("_") if part]
        base = "_".join(parts)
        if not base or not base[0].isalpha():
            base = f"col_{index + 1}"
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f"{base}_{suffix}"
            suffix += 1
        taken.add(candidate)
        safe.append(candidate)
    return safe


def run(ctx: AnalysisContext) -> IngestManifest:
    """S0 수집·정규화 산출물. 실제 헤더와 행 수만 반영한다."""
    # S0 정규화: 겹치는 원본 헤더 이름을 유일하게 만든 뒤 모든 산출물이 이
    # 이름을 참조한다. 대상 이름(safe)도 위치 기반이라 원본 중복과 무관하게 유일하다.
    sources = deduplicate_source_names(ctx.header)
    return IngestManifest(
        files=[
            SourceFile(
                path=ctx.source_name,
                format=IngestFormat.CSV,
                encoding="utf-8",
                size_bytes=ctx.size_bytes,
            )
        ],
        columns=[SourceColumn(name=name, index=i) for i, name in enumerate(sources)],
        row_count=ctx.row_count,
        delimiter=",",
    )


def source_names(ingest: IngestManifest) -> list[str]:
    """S0 산출물에서 소스 이름 목록을 꺼낸다(이후 단계가 쓴다)."""
    return [column.name for column in ingest.columns]


__all__ = ["deduplicate_source_names", "run", "sanitize_names", "source_names"]
