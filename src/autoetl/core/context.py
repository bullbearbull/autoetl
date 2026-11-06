"""P1 분석 컨텍스트 — 원본 판독과 DuckDB 적재를 소유한다.

AnalysisContext 하나가 파이프라인 실행 전체의 입력 상태를 뜻한다(T-0021):

- csv 모듈로 원본을 한 번 읽어 헤더(정규화: strip·빈 이름 col_N 채움)·행 수·
  크기를 확정한다. 오류 경로(UnicodeDecodeError, InputError)는 v0 러너와 같다.
- 프로세스 안 DuckDB 연결 하나에 원본을 **전 값 VARCHAR** 테이블(`raw`)로
  적재한다. 원문은 바꾸지 않는다: 앞자리 0('007'), 지수 표기('1E5'),
  공백만 있는 값('  ')은 그대로 남고, 따옴표 없는 빈 칸은 NULL, 따옴표로
  감싼 빈 문자열("")은 ''로 구분해 남는다(allow_quoted_nulls=false).
- 조회는 내부 식별자(c0, c1, …)를 쓴다. DuckDB가 중복 헤더를 a, a_1로
  고쳐 쓰거나 특수문자를 포함해도 위치 기반 조회에 영향이 없게 뷰(raw_view)로
  감싼다.
- DuckDB 임시 폴더는 작업 임시 폴더 안에 두고 close()(예외로 끝나도) 지운다.

이후 단계가 쓰는 자리: normalized_view(S3 타입 추론이 정규화 뷰 이름을
채운다), stage_artifacts(pipeline이 단계 산출물을 단계 키로 등록한다).
단계는 앞 단계 산출물을 함수 인자로 받는 게 기본 인터페이스다
(core/README.md 참조).
"""

from __future__ import annotations

import csv
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from autoetl.core.errors import InputError

RAW_TABLE = "raw"
"""원본 테이블. 컬럼 이름은 DuckDB가 헤더에서 만든 그대로다(중복은 a_1로 고쳐 쓴다)."""

RAW_VIEW = "raw_view"
"""내부 식별자(c0, c1, …)로 조회하는 원본 뷰. 단계는 이 뷰를 쓴다."""

INTERNAL_ID_OF_POSITION = "c{position}"
"""내부 식별자 형식. 위치가 곧 신원이다(헤더 문자열과 무관)."""


@dataclass(frozen=True)
class ContextColumn:
    """원본 컬럼 하나의 세 가지 이름."""

    position: int
    original_name: str
    column_id: str


def _read_source(path: Path) -> tuple[list[str], int, int]:
    """UTF-8 CSV의 헤더·데이터 행 수·크기를 읽는 최소 S0 입력 파서.

    utf-8-sig로 읽어 엑셀이 붙이는 BOM을 떼어 낸다(BOM이 없으면 그대로).
    행 수는 스트리밍으로 세어 전체 파일을 메모리에 올리지 않는다.
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            first = next(reader, None)
            if first is None:
                raise InputError(f"빈 CSV: {path}")
            header = [name.strip() or f"col_{i + 1}" for i, name in enumerate(first)]
            row_count = sum(1 for _ in reader)
        except csv.Error as exc:
            # 셀 하나가 131072자를 넘거나(NUL 바이트 등) CSV 구문을 못 쓰는 경우.
            # 원문 예외 메시지에 셀 내용이 섞일 수 있으므로 종류와 위치만 안내한다.
            raise InputError(
                f"입력 CSV를 끝까지 읽지 못했다({path}). 마지막으로 읽은 행 번호 "
                f"{reader.line_num}. 이유: {type(exc).__name__} — 셀 하나가 너무 크거나"
                "(csv 필드 한도 131072자) 허용되지 않는 바이트가 들어 있다."
            ) from exc
    return header, row_count, path.stat().st_size


class AnalysisContext:
    """한 번의 파이프라인 실행이 공유하는 입력 상태와 DuckDB 연결.

    컨텍스트 매니저로 쓴다(`with AnalysisContext(path) as ctx:`). 블록이
    끝나면(예외로 끝나도) 연결을 닫고 임시 폴더를 지운다.
    """

    def __init__(
        self,
        input_path: Path,
        *,
        memory_limit: str | None = None,
        threads: int | None = None,
    ) -> None:
        self.input_path = input_path
        self.source_name = input_path.name
        self.header, self.row_count, self.size_bytes = _read_source(input_path)
        self.columns = tuple(
            ContextColumn(
                position=position,
                original_name=name,
                column_id=INTERNAL_ID_OF_POSITION.format(position=position),
            )
            for position, name in enumerate(self.header)
        )
        # 이후 단계가 채우는 자리(모듈 docstring 참조).
        self.normalized_view: str | None = None
        self.stage_artifacts: dict[str, object] = {}

        self._temp_dir = Path(tempfile.mkdtemp(prefix="autoetl-duckdb-"))
        config: dict[str, Any] = {"temp_directory": str(self._temp_dir)}
        if memory_limit is not None:
            config["memory_limit"] = memory_limit
        if threads is not None:
            config["threads"] = threads
        try:
            self.connection = duckdb.connect(database=":memory:", config=config)
            try:
                self.connection.execute(
                    f"CREATE TABLE {RAW_TABLE} AS SELECT * FROM read_csv(?, "
                    "header=true, all_varchar=true, allow_quoted_nulls=false, delim=',')",
                    [str(input_path)],
                )
            except duckdb.Error as exc:
                # 적재 실패(행마다 컬럼 수가 다른 등)도 입력 문제다. DuckDB 오류
                # 원문에 파일 내용이 섞일 수 있으므로 종류만 안내한다(금지 패턴 1·6).
                raise InputError(
                    f"입력 CSV를 적재하지 못했다({input_path}). 이유: "
                    f"{type(exc).__name__} — 행마다 컬럼 수가 다르거나 읽을 수 "
                    "없는 형식이다."
                ) from exc
            duckdb_names = [
                row[0] for row in self.connection.execute(f"DESCRIBE {RAW_TABLE}").fetchall()
            ]
            if len(duckdb_names) != len(self.columns):
                raise InputError(
                    f"입력 CSV의 컬럼 수가 헤더와 다르다({input_path}): "
                    f"헤더 {len(self.columns)}개, 데이터 행 {len(duckdb_names)}개."
                )
            select_list = ", ".join(
                f'"{name}" AS "{column.column_id}"'
                for name, column in zip(duckdb_names, self.columns, strict=True)
            )
            self.connection.execute(
                f"CREATE VIEW {RAW_VIEW} AS SELECT {select_list} FROM {RAW_TABLE}"
            )
        except BaseException:
            self.close()
            raise

    def column_id_of(self, position: int) -> str:
        """위치 → 내부 식별자. 단계 쿼리는 이 이름으로 원본 컬럼을 가리킨다."""
        return self.columns[position].column_id

    def close(self) -> None:
        """연결을 닫고 임시 폴더를 지운다. 여러 번 불러도 안전하다."""
        connection = getattr(self, "connection", None)
        if connection is not None:
            connection.close()
        temp_dir = getattr(self, "_temp_dir", None)
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    def __enter__(self) -> AnalysisContext:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
