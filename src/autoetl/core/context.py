"""P1 분석 컨텍스트 — 원본 판독과 DuckDB 적재를 소유한다.

AnalysisContext 하나가 파이프라인 실행 전체의 입력 상태를 뜻한다(T-0021):

- 입력 추론은 S0의 probe_source(T-0023)가 맡는다: 인코딩(BOM → UTF-8 엄격 →
  charset-normalizer, 비 UTF-8은 임시 UTF-8 파일로 변환), 구분자, 헤더 행 수.
  오류 경로(빈 CSV·CSV 구문 오류 → InputError)는 v0 러너와 같다.
- 프로세스 안 DuckDB 연결 하나에 원본을 **전 값 VARCHAR** 테이블(`raw`)로
  적재한다. 원문은 바꾸지 않는다: 앞자리 0('007'), 지수 표기('1E5'),
  공백만 있는 값('  ')은 그대로 남고, 따옴표 없는 빈 칸은 NULL, 따옴표로
  감싼 빈 문자열("")은 ''로 구분해 남는다(allow_quoted_nulls=false).
- **파일에서 온 문자열은 SQL 문장에 넣지 않는다.** 적재할 때부터 컬럼 이름을
  코드가 만든 내부 식별자(c0, c1, …)로 지정하고, 원래 헤더 이름은 Python
  쪽(ContextColumn)에만 둔다. 조작된 헤더로 다른 SQL 문장을 실행시키거나
  특수문자(큰따옴표 등) 때문에 적재가 깨지는 일이 없다.
- 연결 기본값을 잠근다(심층 방어): 확장 자동 설치·자동 로드 끔, 원본 적재가
  끝나면 외부 접근(파일 함수·ATTACH) 끔. 이후 단계 쿼리는 메모리 테이블만
  볼 수 있다.
- DuckDB 임시 폴더와 인코딩 변환 임시 파일은 close()(예외로 끝나도) 지운다.

이후 단계가 쓰는 자리: normalized_view(S3 타입 추론이 정규화 뷰 이름을
채운다), stage_artifacts(pipeline이 단계 산출물을 단계 키로 등록한다).
단계는 앞 단계 산출물을 함수 인자로 받는 게 기본 인터페이스다
(core/README.md 참조).
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from autoetl.core.errors import InputError
from autoetl.core.stages.s00_ingest import SourceSpec, probe_source

RAW_TABLE = "raw"
"""원본 테이블. 컬럼 이름은 코드가 만든 내부 식별자(c0, c1, …)다."""

INTERNAL_ID_OF_POSITION = "c{position}"
"""내부 식별자 형식. 위치가 곧 신원이다(헤더 문자열과 무관)."""


def quote_identifier(identifier: str) -> str:
    """SQL 식별자를 안전하게 큰따옴표로 감싼다(내부의 따옴표는 두 번).

    단계 쿼리에서 식별자를 인용해야 할 때는 반드시 이 함수를 쓴다
    (core/README.md 규약). 원본 헤더 문자열을 식별자로 쓰지 않는 규칙은
    그대로다 — 내부 식별자(c0, …)나 단계가 만든 이름에만 쓴다.
    """
    return '"' + identifier.replace('"', '""') + '"'


@dataclass(frozen=True)
class ContextColumn:
    """원본 컬럼 하나의 세 가지 이름."""

    position: int
    original_name: str
    column_id: str


class AnalysisContext:
    """한 번의 파이프라인 실행이 공유하는 입력 상태와 DuckDB 연결.

    컨텍스트 매니저로 쓴다(`with AnalysisContext(path) as ctx:`). 블록이
    끝나면(예외로 끝나도) 연결을 닫고 임시 폴더와 인코딩 변환 임시 파일을 지운다.

    입력 추론(spec)을 넘기지 않으면 probe_source(S0 추론)로 직접 판정한다
    (T-0023: 인코딩·구분자·헤더 행 수). pipeline은 probe 결과를 재사용해
    두 번 판정하지 않는다.
    """

    def __init__(
        self,
        input_path: Path,
        *,
        spec: SourceSpec | None = None,
        memory_limit: str | None = None,
        threads: int | None = None,
    ) -> None:
        self.input_path = input_path
        self.source_name = input_path.name
        self.spec = probe_source(input_path) if spec is None else spec
        self.header = list(self.spec.header)
        self.row_count = self.spec.row_count
        self.size_bytes = self.spec.size_bytes
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
        config: dict[str, Any] = {
            "temp_directory": str(self._temp_dir),
            # P1 규칙: core는 네트워크·확장 없이 돈다. 확장 자동 설치·로드 잠금.
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        }
        if memory_limit is not None:
            config["memory_limit"] = memory_limit
        if threads is not None:
            config["threads"] = threads
        try:
            self.connection = duckdb.connect(database=":memory:", config=config)
            try:
                # 파일에서 온 문자열(헤더)은 SQL에 넣지 않는다. 컬럼 이름을 코드가
                # 만든 내부 식별자(c0, c1, …)로 지정해 적재하고 원래 헤더 이름은
                # ContextColumn(Python)에만 남는다. 식별자는 [c][0-9] 글자뿐이라
                # 문장 조립이 안전하다. 구분자도 후보 집합(, ; 탭 |) 안의 한 글자다.
                # 헤더 행은 probe가 이미 셌으므로 skip으로 건너뛴다(T-0023).
                columns_spec = ", ".join(
                    f"'{column.column_id}': 'VARCHAR'" for column in self.columns
                )
                self.connection.execute(
                    f"CREATE TABLE {RAW_TABLE} AS SELECT * FROM read_csv(?, "
                    f"header=false, skip={self.spec.header_row_count}, "
                    f"columns={{{columns_spec}}}, "
                    f"allow_quoted_nulls=false, delim='{self.spec.delimiter}')",
                    [str(self.spec.load_path)],
                )
            except duckdb.Error as exc:
                # 적재 실패(행마다 컬럼 수가 다른 등)도 입력 문제다. DuckDB 오류
                # 원문에 파일 내용이 섞일 수 있으므로 종류만 안내한다(금지 패턴 1·6).
                raise InputError(
                    f"입력 CSV를 적재하지 못했다({input_path}). 이유: "
                    f"{type(exc).__name__} — 행마다 컬럼 수가 다르거나 읽을 수 "
                    "없는 형식이다."
                ) from exc
            # 원본 적재가 끝났으니 외부 접근(파일 함수·ATTACH)을 끈다. 이후 단계
            # 쿼리는 메모리 테이블만 볼 수 있다(심층 방어).
            self.connection.execute("SET enable_external_access = false")
        except BaseException:
            self.close()
            raise

    def column_id_of(self, position: int) -> str:
        """위치 → 내부 식별자. 단계 쿼리는 이 이름으로 원본 컬럼을 가리킨다."""
        return self.columns[position].column_id

    def close(self) -> None:
        """연결을 닫고 임시 폴더와 변환 임시 파일을 지운다. 여러 번 불러도 안전하다."""
        connection = getattr(self, "connection", None)
        if connection is not None:
            connection.close()
        temp_dir = getattr(self, "_temp_dir", None)
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        spec = getattr(self, "spec", None)
        if spec is not None and spec.converted:
            spec.load_path.unlink(missing_ok=True)

    def __enter__(self) -> AnalysisContext:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
