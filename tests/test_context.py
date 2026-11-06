"""AnalysisContext 검증(T-0021) — 원문 보존·적재 규모·임시 폴더 수명."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoetl.core.context import RAW_VIEW, AnalysisContext
from autoetl.core.errors import InputError


def _values(ctx: AnalysisContext, column_id: str) -> list[object]:
    rows = ctx.connection.execute(f'SELECT "{column_id}" FROM {RAW_VIEW}').fetchall()
    return [row[0] for row in rows]


def test_loads_100k_rows_with_counts(tmp_path: Path) -> None:
    # 10만 행 적재: 행 수·컬럼 수가 정확해야 한다(카운트도 테이블에서 직접).
    rows = 100_000
    csv_path = tmp_path / "big.csv"
    lines = ["id,value,label"]
    lines += [f"{i},v{i},라벨{i % 7}" for i in range(rows)]
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with AnalysisContext(csv_path) as ctx:
        assert ctx.row_count == rows
        assert len(ctx.columns) == 3
        counted = ctx.connection.execute(f"SELECT COUNT(*) FROM {RAW_VIEW}").fetchone()
        assert counted is not None and counted[0] == rows


def test_preserves_raw_values_verbatim(tmp_path: Path) -> None:
    # 원문은 바꾸지 않는다: 앞자리 0, 지수 표기, 공백만 있는 값.
    csv_path = tmp_path / "raw.csv"
    csv_path.write_text(
        'a,b,c,d\n007,"1E5","  ","0.10"\n00,"1e-3","   ","3.00"\n',
        encoding="utf-8",
    )
    with AnalysisContext(csv_path) as ctx:
        assert _values(ctx, "c0") == ["007", "00"]
        assert _values(ctx, "c1") == ["1E5", "1e-3"]
        assert _values(ctx, "c2") == ["  ", "   "]
        assert _values(ctx, "c3") == ["0.10", "3.00"]


def test_distinguishes_empty_string_from_missing(tmp_path: Path) -> None:
    # 따옴표 없는 빈 칸은 NULL(결측), 따옴표로 감싼 ""는 빈 문자열로 구분해 남는다.
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text('a,b\n"",\n"x",""\n', encoding="utf-8")
    with AnalysisContext(csv_path) as ctx:
        assert _values(ctx, "c0") == ["", "x"]
        assert _values(ctx, "c1") == [None, ""]


def test_duplicate_headers_map_by_position(tmp_path: Path) -> None:
    # 중복 헤더(a, a)는 DuckDB가 a, a_1로 고쳐 쓴다. 내부 식별자는 위치 기반이라
    # 영향받지 않는다(벤치 multi_header 변형이 이 경우다).
    csv_path = tmp_path / "dup.csv"
    csv_path.write_text("a,a,b\n1,2,3\n4,5,6\n", encoding="utf-8")
    with AnalysisContext(csv_path) as ctx:
        assert [column.original_name for column in ctx.columns] == ["a", "a", "b"]
        assert [column.column_id for column in ctx.columns] == ["c0", "c1", "c2"]
        assert _values(ctx, "c0") == ["1", "4"]
        assert _values(ctx, "c1") == ["2", "5"]


def test_temp_dir_removed_after_success_and_failure(tmp_path: Path) -> None:
    # 임시 폴더는 정상 종료와 예외 종료 모두에서 지워진다.
    csv_path = tmp_path / "ok.csv"
    csv_path.write_text("a\n1\n", encoding="utf-8")
    import tempfile

    before = set(Path(tempfile.gettempdir()).glob("autoetl-duckdb-*"))
    with AnalysisContext(csv_path):
        pass
    after_success = set(Path(tempfile.gettempdir()).glob("autoetl-duckdb-*"))
    assert after_success == before

    with pytest.raises(RuntimeError), AnalysisContext(csv_path) as ctx:
        assert ctx.row_count == 1
        raise RuntimeError("단계 폭발")
    after_failure = set(Path(tempfile.gettempdir()).glob("autoetl-duckdb-*"))
    assert after_failure == before


def test_temp_dir_removed_when_load_fails(tmp_path: Path) -> None:
    # 적재 자체가 실패해도(행마다 컬럼 수가 다름) 생성자가 정리한다.
    # 적재 오류는 입력 문제로 InputError가 난다(원문 미포함 안내).
    csv_path = tmp_path / "ragged.csv"
    csv_path.write_text("a,b\n1\n", encoding="utf-8")  # 두 번째 행이 1칸뿐이다
    import tempfile

    before = set(Path(tempfile.gettempdir()).glob("autoetl-duckdb-*"))
    with pytest.raises(InputError, match="적재하지 못했다"):
        AnalysisContext(csv_path)
    after = set(Path(tempfile.gettempdir()).glob("autoetl-duckdb-*"))
    assert after == before


def test_bom_stripped_like_pipeline(tmp_path: Path) -> None:
    # BOM은 떼어 낸다(v0 러너의 utf-8-sig 판독과 같은 결과).
    csv_path = tmp_path / "bom.csv"
    csv_path.write_text("customer_id,amount\n1,100\n", encoding="utf-8-sig")
    with AnalysisContext(csv_path) as ctx:
        assert ctx.header[0] == "customer_id"
        assert _values(ctx, "c0") == ["1"]


def test_resource_settings_accepted(tmp_path: Path) -> None:
    # 메모리 한도·스레드 수 설정 자리(값은 DuckDB 설정으로 전달된다).
    csv_path = tmp_path / "cfg.csv"
    csv_path.write_text("a\n1\n", encoding="utf-8")
    with AnalysisContext(csv_path, memory_limit="1GB", threads=1) as ctx:
        threads = ctx.connection.execute("SELECT current_setting('threads')").fetchone()
        assert threads is not None and threads[0] == 1
        memory = ctx.connection.execute("SELECT current_setting('memory_limit')").fetchone()
        assert memory is not None
        # 1GB(10^9 바이트)는 MiB 단위로 환산해 대략 954다.
        mib = float(str(memory[0]).split()[0])
        assert 900 <= mib <= 1000, memory[0]


def test_input_errors_preserved(tmp_path: Path) -> None:
    # v0 러너와 같은 오류 경로: 빈 CSV·과대 셀은 InputError.
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(InputError, match="빈 CSV"):
        AnalysisContext(empty)

    huge = tmp_path / "huge.csv"
    huge.write_text("a,b\n1," + "x" * 200_000 + "\n", encoding="utf-8")
    with pytest.raises(InputError, match="읽지 못했다"):
        AnalysisContext(huge)
