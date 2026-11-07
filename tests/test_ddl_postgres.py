"""PostgreSQL DDL 생성기 검증(T-0030) — 완료 기준 전항."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlglot import exp
from sqlglot import parse as sqlglot_parse

from autoetl.contracts import (
    ColumnProfile,
    ColumnStats,
    LengthStats,
    NumericStats,
    OutlierMethod,
    OutlierSummary,
    PhysicalType,
    SamplingPlan,
    SchemaCandidate,
    SchemaPattern,
    StatsKind,
    TargetColumn,
    TargetTable,
)
from autoetl.core.ddl import postgres as pg

DOC = Path(__file__).resolve().parent.parent / "docs" / "ddl-postgres.md"

FULL = SamplingPlan(strategy="full", total_rows=100, rationale="테스트 전수")
SAMPLE = SamplingPlan(
    strategy="sample", total_rows=5_000_000, sample_rows=500, seed=42, rationale="테스트 표본"
)


def _column(
    name: str,
    kind: PhysicalType,
    *,
    nullable: bool = True,
    is_primary_key: bool = False,
    foreign_key: str | None = None,
    precision: int | None = None,
    scale: int | None = None,
    source: str | None = None,
) -> TargetColumn:
    return TargetColumn(
        name=name,
        physical_type=kind,
        source_columns=[source or name],
        nullable=nullable,
        is_primary_key=is_primary_key,
        foreign_key=foreign_key,
        precision=precision,
        scale=scale,
    )


def _profile(column: str, maximum: int) -> ColumnProfile:
    return ColumnProfile(
        column=column,
        count=10,
        missing_count=0,
        missing_rate=0.0,
        unique_count=4,
        cardinality_ratio=0.4,
        length=LengthStats(minimum=1, maximum=maximum, mean=3.0, p50=3.0, p95=3.0),
    )


def _stat(column: str, minimum: float, maximum: float) -> ColumnStats:
    return ColumnStats(
        column=column,
        kind=StatsKind.NUMERIC,
        numeric=NumericStats(
            mean=0.0,
            std=1.0,
            minimum=minimum,
            maximum=maximum,
            p25=0.0,
            p50=0.0,
            p75=0.0,
            skew=0.0,
            max_decimal_digits=0,
            outliers=OutlierSummary(
                method=OutlierMethod.IQR, count=0, rate=0.0, bounds="IQR 1.5배: [0.0, 1.0]"
            ),
        ),
    )


def _candidate(*tables: TargetTable) -> SchemaCandidate:
    return SchemaCandidate(
        id="C1", pattern=SchemaPattern.THIRD_NORMAL_FORM, tables=list(tables), rationale="테스트"
    )


def _tables_in(ddl: str) -> list[str]:
    statements = [s for s in sqlglot_parse(ddl, read="postgres") if s is not None]
    creates: list[exp.Create] = [s for s in statements if isinstance(s, exp.Create)]
    return [str(create.this.this.name) for create in creates]


# ── 완료 기준: 모든 물리 타입·복합 PK·복합 FK 픽스처 ─────────────────────


def test_all_physical_types_roundtrip() -> None:
    observations: dict[str, ColumnProfile | ColumnStats] = {
        "c_integer": _stat("c_integer", -100, 999),
        "c_string": _profile("c_string", 10),
    }
    kinds = list(PhysicalType)  # 11종 전부
    columns = [_column(f"c_{kind.value}", kind, precision=12, scale=2) for kind in kinds]
    table = TargetTable(name="all_types", columns=columns)
    candidate = _candidate(table)
    ddl = pg.generate_ddl(
        candidate,
        profiles=[p for p in observations.values() if isinstance(p, ColumnProfile)],
        stats=[s for s in observations.values() if isinstance(s, ColumnStats)],
        sampling=FULL,  # 관측 한도는 전수일 때만 반영된다
    )
    summaries = pg.verify_ddl(ddl, candidate)
    assert summaries == [
        {"table": "all_types", "columns": len(kinds), "primary_key": [], "foreign_keys": 0}
    ]
    for expected in (
        '"c_integer" INT',  # sqlglot 정규 표기(INT = INTEGER 동의어)
        "DECIMAL(14, 3)",  # precision 12 + 2, scale 2 + 1
        "DOUBLE PRECISION",
        "BOOLEAN",
        "VARCHAR(30)",  # 관측 최대 10 + 20
        "DATE",
        "TIME",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "JSONB",
        "BYTEA",
    ):
        assert expected in ddl, f"{expected} 빠졌다"


def test_compound_pk_and_compound_fk_roundtrip() -> None:
    customers = TargetTable(
        name="customers",
        columns=[_column("cust_id", PhysicalType.INTEGER, nullable=False, is_primary_key=True)],
    )
    orders = TargetTable(
        name="orders",
        columns=[
            _column("order_id", PhysicalType.INTEGER, nullable=False, is_primary_key=True),
            _column(
                "cust_id", PhysicalType.INTEGER, nullable=False, foreign_key="customers(cust_id)"
            ),
        ],
    )
    items = TargetTable(
        name="items",
        columns=[
            _column(
                "order_id",
                PhysicalType.INTEGER,
                nullable=False,
                is_primary_key=True,
                foreign_key="orders(order_id)",
            ),
            _column("product_id", PhysicalType.INTEGER, nullable=False, is_primary_key=True),
            _column("quantity", PhysicalType.INTEGER),
        ],
    )
    links = TargetTable(
        name="links",
        columns=[
            _column("link_id", PhysicalType.INTEGER, nullable=False, is_primary_key=True),
            _column(
                "order_id", PhysicalType.INTEGER, nullable=False, foreign_key="items(order_id)"
            ),
            _column(
                "product_id", PhysicalType.INTEGER, nullable=False, foreign_key="items(product_id)"
            ),
        ],
    )
    # 정의는 자식 먼저 — DDL은 FK 의존 순서(부모 먼저)로 낸다.
    candidate = _candidate(links, items, orders, customers)
    ddl = pg.generate_ddl(candidate)
    summaries = pg.verify_ddl(ddl, candidate)
    by_table: dict[str, dict[str, object]] = {
        str(summary["table"]): summary for summary in summaries
    }
    assert by_table["items"]["primary_key"] == ["order_id", "product_id"]  # 복합 PK
    assert by_table["links"]["foreign_keys"] == 1  # 같은 부모의 두 컬럼 → 복합 FK 1개
    assert by_table["orders"]["foreign_keys"] == 1
    order = [str(summary["table"]) for summary in summaries]
    assert order == ["customers", "orders", "items", "links"]  # 의존 순서 + 이름순
    assert (
        'FOREIGN KEY ("order_id", "product_id") REFERENCES "items"("order_id", "product_id")' in ddl
    )
    assert len(_tables_in(ddl)) == 4  # 추가 구문 없음


# ── 완료 기준: 위험한 이름 픽스처(주입 불가) ──────────────────────────────


def test_reserved_and_overlong_names_roundtrip() -> None:
    overlong = "a" * 70  # 63바이트 초과(ASCII)
    table = TargetTable(
        name="order",  # 예약어
        columns=[
            _column("user", PhysicalType.INTEGER, is_primary_key=True),  # 예약어 컬럼
            _column("select", PhysicalType.STRING),
            _column(overlong, PhysicalType.INTEGER),
        ],
    )
    candidate = _candidate(table)
    ddl = pg.generate_ddl(candidate)
    summaries = pg.verify_ddl(ddl, candidate)
    assert len(summaries) == 1
    assert summaries[0]["columns"] == 3
    tables = _tables_in(ddl)
    assert tables == ["order"]  # 추가 구문 없음(주입 불가)
    shortened = pg._shorten(overlong)
    assert len(shortened.encode("utf-8")) <= 63
    assert shortened != overlong
    assert shortened[-9] == "_" and re.fullmatch(r"[0-9a-f]{8}", shortened[-8:])
    assert f'"{shortened}"' in ddl
    assert '"order"' in ddl and '"user"' in ddl


def test_contract_rejects_dangerous_names() -> None:
    # 계약 패턴(^[a-z][a-z0-9_]*$)이 1차 방어다 — 한글·공백·따옴표·세미콜론은
    # 후보에 들어올 수 없다(ValidationError).
    for name in ("고객", "a b", 'x"; DROP TABLE t; --', "a;b"):
        with pytest.raises(ValidationError):
            TargetTable(name=name, columns=[_column("c", PhysicalType.INTEGER)])
        with pytest.raises(ValidationError):
            TargetTable(name="t", columns=[_column(name, PhysicalType.INTEGER)])


def test_identifier_quoting_blocks_injection() -> None:
    # 계약 밖 경로 방어(완료 기준: '생성 DDL을 파싱하면'): 계약 검사를 건너뛴
    # 후보(model_construct)로 generate_ddl을 돌려 재파싱으로 단정한다.
    # 세미콜론·주석·작은따옴표·역슬래시·공백·한글·예약어가 들어 있어도
    # 문장 수와 이름이 그대로다(추가 구문이 끼어들 수 없다).
    names = [
        'x"; DROP TABLE t; --',  # 따옴표·세미콜론·주석
        "a b",  # 공백
        "고객" * 30,  # 한글 90바이트(UTF-8) — 63 초과 축소
        "order",  # 예약어
        "user",
        "select",
        "it's",  # 작은따옴표
        "back\\slash",  # 역슬래시
    ]
    columns = [
        TargetColumn.model_construct(
            name=name,
            physical_type=PhysicalType.INTEGER,
            precision=None,
            scale=None,
            source_columns=[name],
            nullable=True,
            is_primary_key=False,
            foreign_key=None,
            description="",
        )
        for name in names
    ]
    table = TargetTable.model_construct(name="order", columns=columns)
    candidate = SchemaCandidate(
        id="C1", pattern=SchemaPattern.THIRD_NORMAL_FORM, tables=[table], rationale="주입 시험"
    )
    evil_schema = 's"; DROP SCHEMA z; --'
    ddl = pg.generate_ddl(candidate, schema=evil_schema)
    statements = [s for s in sqlglot_parse(ddl, read="postgres") if s is not None]
    assert len(statements) == 1  # 문장 1개 — 추가 구문 없음(주입 불가)
    create = statements[0]
    assert isinstance(create, exp.Create)
    assert create.this.this.db == evil_schema  # 스키마 이름도 보존
    assert create.this.this.name == "order"
    column_defs = create.this.expressions
    assert len(column_defs) == len(names)
    safe = pg._identifier_map(names)
    for column_def, name in zip(column_defs, names, strict=True):
        assert column_def.name == safe[name]  # 이름 보존(축소 맵 포함)
        assert len(column_def.name.encode("utf-8")) <= 63
    pg.verify_ddl(ddl, candidate, schema=evil_schema)  # 전수 대조도 통과


# ── 완료 기준: 매핑 문서 표와 코드 일치 ──────────────────────────────────


def test_mapping_doc_matches_code() -> None:
    doc = DOC.read_text("utf-8")
    static_section = doc.split("관측값 없이 정해지는 타입")[1].split("관측값으로 정하는 타입")[0]
    rows = dict(re.findall(r"^\| ([a-z]+) \| ([A-Z ]+) \|$", static_section, re.M))
    assert rows == {kind.value: name for kind, name in pg.TYPE_MAP.items()}

    settings_section = doc.split("## 여유·상한 설정")[1].split("## 식별자")[0]
    values = dict(re.findall(r"^\| ([^|]+?) \| (-?\d+) \|$", settings_section, re.M))
    assert int(values["numeric precision 여유"]) == pg.NUMERIC_PRECISION_MARGIN
    assert int(values["numeric scale 여유"]) == pg.NUMERIC_SCALE_MARGIN
    assert int(values["numeric 정밀도 상한"]) == pg.NUMERIC_LIMIT
    assert int(values["varchar 길이 여유"]) == pg.VARCHAR_MARGIN
    assert int(values["varchar 상한(초과 시 TEXT)"]) == pg.VARCHAR_TEXT_LIMIT
    assert int(values["식별자 길이 상한(바이트)"]) == pg.MAX_IDENTIFIER_BYTES
    assert int(values["이름 축소 해시 접미(sha256 16진수)"]) == pg.HASH_SUFFIX_CHARS
    assert int(values["int4 상한"]) == pg.INT4_MAX
    assert int(values["int4 하한"]) == pg.INT4_MIN


# ── 완료 기준: 같은 후보는 같은 DDL 바이트 ──────────────────────────────


def test_same_candidate_yields_same_bytes() -> None:
    table = TargetTable(
        name="t",
        columns=[
            _column("id", PhysicalType.INTEGER, nullable=False, is_primary_key=True),
            _column("name", PhysicalType.STRING),
        ],
    )
    candidate = _candidate(table)
    first = pg.generate_ddl(candidate, profiles=[_profile("name", 12)])
    assert pg.generate_ddl(candidate, profiles=[_profile("name", 12)]) == first
    # 프로파일 순서는 결과에 영향을 주지 않는다(컬럼 기준 맵).
    assert pg.generate_ddl(candidate, profiles=[_profile("id", 12), _profile("name", 12)]) == first


# ── 타입 매핑 규칙(동적 3종) ─────────────────────────────────────────────


def test_integer_range_selects_type() -> None:
    def ddl_for(stat: ColumnStats | None, sampling: SamplingPlan | None) -> str:
        candidate = _candidate(TargetTable(name="t", columns=[_column("n", PhysicalType.INTEGER)]))
        if stat is None:
            return pg.generate_ddl(candidate, sampling=sampling)
        return pg.generate_ddl(candidate, stats=[stat], sampling=sampling)

    in_range = _stat("n", -2147483648, 2147483647)
    assert '"n" INT' in ddl_for(in_range, FULL)  # 전수 관측 — int4 안
    assert "BIGINT" in ddl_for(_stat("n", 0, 2147483648), FULL)  # 경계 초과
    assert "BIGINT" in ddl_for(_stat("n", -2147483649, 0), FULL)
    assert "BIGINT" in ddl_for(None, FULL)  # 범위 미상 — 안전 쪽
    # 표본 관측·계획 없음은 한도에 쓰지 않는다(표본 최댓값 < 전수 최댓값 가능)
    assert "BIGINT" in ddl_for(in_range, SAMPLE)
    assert "BIGINT" in ddl_for(in_range, None)


def test_decimal_precision_rules() -> None:
    def ddl_for(precision: int | None, scale: int | None) -> str:
        return pg.generate_ddl(
            _candidate(
                TargetTable(
                    name="t",
                    columns=[_column("d", PhysicalType.DECIMAL, precision=precision, scale=scale)],
                )
            )
        )

    assert "DECIMAL(12, 3)" in ddl_for(10, 2)  # 여유 +2/+1
    assert "DECIMAL(14, 3)" in ddl_for(12, 2)
    assert re.search(r"\bDECIMAL\b(?!\()", ddl_for(None, 3))  # precision 미상 — 무제한
    # 상한 38에 걸리면 scale 여유를 포기해 정수부 자릿수(p-s)·scale을 지킨다
    assert "DECIMAL(38, 10)" in ddl_for(38, 10)
    assert "DECIMAL(38, 37)" in ddl_for(38, 37)  # (38, 38) 정수부 0자리 방지
    assert "DECIMAL(38, 11)" in ddl_for(37, 10)  # 상한에 안 걸리면 여유가 들어간다


def test_string_length_rules() -> None:
    def ddl_for(maximum: int | None, sampling: SamplingPlan | None) -> str:
        candidate = _candidate(TargetTable(name="t", columns=[_column("s", PhysicalType.STRING)]))
        if maximum is None:
            return pg.generate_ddl(candidate, sampling=sampling)
        return pg.generate_ddl(candidate, profiles=[_profile("s", maximum)], sampling=sampling)

    assert "VARCHAR(30)" in ddl_for(10, FULL)  # 전수 관측 최대 + 20
    assert "TEXT" in ddl_for(None, FULL)  # 길이 미상
    assert "TEXT" in ddl_for(pg.VARCHAR_TEXT_LIMIT, FULL)  # varchar 상한 초과
    # 표본 관측·계획 없음 — 표본 최댓값을 한도로 쓰면 적재가 깨진다
    assert "TEXT" in ddl_for(10, SAMPLE)
    assert "TEXT" in ddl_for(10, None)


# ── 실패 닫힘: FK 지정·순환·중복·축소 충돌 ───────────────────────────────


def test_fk_spec_format_is_enforced() -> None:
    with pytest.raises(ValueError, match="FK 지정 형식"):
        pg._parse_foreign_key("items(a, b)")  # 복합은 컬럼 단위 지정으로만
    with pytest.raises(ValueError, match="FK 지정 형식"):
        pg._parse_foreign_key("items")


def test_fk_cycle_and_unknown_parent_raise() -> None:
    a = TargetTable(
        name="a",
        columns=[
            _column("id", PhysicalType.INTEGER, is_primary_key=True),
            _column("b_id", PhysicalType.INTEGER, foreign_key="b(id)"),
        ],
    )
    b = TargetTable(
        name="b",
        columns=[
            _column("id", PhysicalType.INTEGER, is_primary_key=True),
            _column("a_id", PhysicalType.INTEGER, foreign_key="a(id)"),
        ],
    )
    with pytest.raises(ValueError, match="FK 순환"):
        pg.generate_ddl(_candidate(a, b))

    orphan = TargetTable(
        name="c",
        columns=[_column("ghost_id", PhysicalType.INTEGER, foreign_key="ghost(id)")],
    )
    with pytest.raises(ValueError, match="후보에 없다"):
        pg.generate_ddl(_candidate(orphan))


def test_independent_fks_to_same_parent_stay_separate() -> None:
    # 홈·원정 팀처럼 같은 부모를 향하는 독립 FK는 복합으로 묶이면 성립하지
    # 않는다(FOREIGN KEY (home, away) REFERENCES team(team_id, team_id)) —
    # 단일 PK 부모는 자식 컬럼마다 FK를 따로 낸다.
    teams = TargetTable(
        name="teams",
        columns=[_column("team_id", PhysicalType.INTEGER, nullable=False, is_primary_key=True)],
    )
    games = TargetTable(
        name="games",
        columns=[
            _column("game_id", PhysicalType.INTEGER, nullable=False, is_primary_key=True),
            _column("home_team_id", PhysicalType.INTEGER, foreign_key="teams(team_id)"),
            _column("away_team_id", PhysicalType.INTEGER, foreign_key="teams(team_id)"),
        ],
    )
    candidate = _candidate(teams, games)
    ddl = pg.generate_ddl(candidate)
    assert ddl.count("FOREIGN KEY") == 2  # 독립 FK 2개
    assert 'FOREIGN KEY ("home_team_id") REFERENCES "teams"("team_id")' in ddl
    assert 'FOREIGN KEY ("away_team_id") REFERENCES "teams"("team_id")' in ddl
    summaries = pg.verify_ddl(ddl, candidate)
    assert [summary["foreign_keys"] for summary in summaries] == [0, 2]


def test_fk_reference_rules_are_enforced() -> None:
    def generate(child: TargetColumn, *parent_columns: TargetColumn) -> str:
        parent = TargetTable(name="parent", columns=list(parent_columns))
        child_table = TargetTable(name="child", columns=[child])
        return pg.generate_ddl(_candidate(parent, child_table))

    pk = _column("pid", PhysicalType.INTEGER, nullable=False, is_primary_key=True)
    # 비PK 참조 — UNIQUE 제약을 만들지 않아 FK가 성립하지 않는다
    with pytest.raises(ValueError, match="부모 PK가 아니다"):
        generate(
            _column("ref", PhysicalType.INTEGER, foreign_key="parent(name)"),
            pk,
            _column("name", PhysicalType.STRING),
        )
    # 없는 컬럼 참조
    with pytest.raises(ValueError, match="부모에 없다"):
        generate(_column("ref", PhysicalType.INTEGER, foreign_key="parent(ghost)"), pk)
    # 자식·부모 물리 타입 불일치
    with pytest.raises(ValueError, match="타입 불일치"):
        generate(_column("ref", PhysicalType.STRING, foreign_key="parent(pid)"), pk)
    # 복합 PK 부모를 일부만(겹쳐서) 가리킴
    with pytest.raises(ValueError, match="한 번씩 가리키지 않는다"):
        parent = TargetTable(
            name="parent",
            columns=[
                _column("a", PhysicalType.INTEGER, nullable=False, is_primary_key=True),
                _column("b", PhysicalType.INTEGER, nullable=False, is_primary_key=True),
            ],
        )
        child = TargetTable(
            name="child",
            columns=[
                _column("x", PhysicalType.INTEGER, foreign_key="parent(a)"),
                _column("y", PhysicalType.INTEGER, foreign_key="parent(a)"),  # b가 빠지고 a가 겹침
            ],
        )
        pg.generate_ddl(_candidate(parent, child))


def test_fk_child_column_uses_parent_type() -> None:
    # 부모·자식이 각자 관측값으로 타입을 정하면 INT·BIGINT가 섞인다 —
    # FK 자식 컬럼은 부모 컬럼과 같은 PostgreSQL 타입으로 낸다.
    teams = TargetTable(
        name="teams",
        columns=[_column("team_id", PhysicalType.INTEGER, nullable=False, is_primary_key=True)],
    )
    games = TargetTable(
        name="games",
        columns=[
            _column("game_id", PhysicalType.INTEGER, nullable=False, is_primary_key=True),
            _column("home_team_id", PhysicalType.INTEGER, foreign_key="teams(team_id)"),
        ],
    )
    ddl = pg.generate_ddl(
        _candidate(teams, games),
        stats=[_stat("team_id", 0, 3_000_000_000), _stat("home_team_id", 0, 100)],  # 부모만 int4 밖
        sampling=FULL,
    )
    assert '"team_id" BIGINT' in ddl
    assert '"home_team_id" BIGINT' in ddl  # 자식 관측(int4 안)과 무관


def test_verify_ddl_checks_not_null() -> None:
    table = TargetTable(
        name="t",
        columns=[
            _column("id", PhysicalType.INTEGER, nullable=False, is_primary_key=True),
            _column("label", PhysicalType.STRING, nullable=False),
        ],
    )
    candidate = _candidate(table)
    ddl = pg.generate_ddl(candidate)
    pg.verify_ddl(ddl, candidate)  # 원본은 통과
    # NOT NULL을 지운 DDL은 대조에서 걸린다(PK는 PostgreSQL이 암묵 NOT NULL)
    tampered = ddl.replace('"label" TEXT NOT NULL', '"label" TEXT')
    with pytest.raises(ValueError, match="NOT NULL 불일치"):
        pg.verify_ddl(tampered, candidate)


def test_duplicate_column_raises() -> None:
    dup = TargetTable(
        name="t",
        columns=[
            _column("id", PhysicalType.INTEGER, is_primary_key=True),
            _column("id", PhysicalType.STRING),
        ],
    )
    with pytest.raises(ValueError, match="같은 이름의 컬럼"):
        pg.generate_ddl(_candidate(dup))


def test_shorten_collision_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # 서로 다른 긴 이름은 해시 접미가 다르다(결정론·유일성).
    safe = pg._identifier_map(["a" * 70, "b" * 70])
    assert safe["a" * 70] != safe["b" * 70]
    assert all(len(name.encode("utf-8")) <= 63 for name in safe.values())
    # 축소 결과가 실제로 겹치면 실패 닫힘이다.
    monkeypatch.setattr(pg, "_shorten", lambda name: "clash")
    with pytest.raises(ValueError, match="축소 충돌"):
        pg._identifier_map(["a" * 70, "b" * 70])


def test_schema_qualification() -> None:
    parents = TargetTable(
        name="parents",
        columns=[_column("id", PhysicalType.INTEGER, nullable=False, is_primary_key=True)],
    )
    children = TargetTable(
        name="children",
        columns=[
            _column("id", PhysicalType.INTEGER, nullable=False, is_primary_key=True),
            _column("parent_id", PhysicalType.INTEGER, nullable=False, foreign_key="parents(id)"),
        ],
    )
    candidate = _candidate(children, parents)
    ddl = pg.generate_ddl(candidate, schema="analytics")
    assert '"analytics"."parents"' in ddl
    # REFERENCES도 같은 스키마로 한정한다 — 스키마 없는 이름은 search_path로
    # 풀려 다른 테이블에 붙는다.
    assert 'REFERENCES "analytics"."parents"("id")' in ddl
    assert 'REFERENCES "parents"' not in ddl
    pg.verify_ddl(ddl, candidate, schema="analytics")
