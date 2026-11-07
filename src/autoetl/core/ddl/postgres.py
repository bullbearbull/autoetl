"""PostgreSQL DDL 생성기와 타입 매핑(T-0030).

sqlglot 표현식 트리로 CREATE TABLE을 만든다(리드 결정). 문자열 이어 붙이기는
쓰지 않는다 — 식별자는 항상 큰따옴표로 인용되고(안의 큰따옴표는 이중으로
이스케이프된다), 이름이 어떻게 생겼든 추가 구문이 끼어들 수 없다. 재파싱
대조(verify_ddl)로 생성 결과를 다시 확인한다(실패 닫힘).

타입 매핑·여유 값은 docs/ddl-postgres.md의 표와 1:1로 일치해야 한다 —
표와 코드가 어긋나면 테스트가 실패한다. 정적 매핑(BOOLEAN·DATE 등)은
TYPE_MAP에, 관측값이 필요한 매핑(integer 범위·decimal 정밀도·varchar 길이)은
_postgres_type이 profile·stats에서 계산한다. 관측 한도는 SamplingPlan이
전수(full)일 때만 쓴다(표본 최댓값은 전수보다 작을 수 있다). FK는 참조 대상이
부모의 PK여야 성립한다 — 단일 PK 부모는 자식 컬럼마다 FK를 따로, 복합 PK
부모는 PK 전부를 한 번씩 가리킬 때만 하나의 복합 FK로 묶는다.

같은 후보·프로파일·통계면 같은 DDL 바이트를 낸다(정렬·이름 축소 모두
결정론적 — 해시 접미는 sha256).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from sqlglot import exp
from sqlglot import parse as sqlglot_parse

from autoetl.contracts import (
    ColumnProfile,
    ColumnStats,
    PhysicalType,
    SamplingPlan,
    SchemaCandidate,
    TargetColumn,
    TargetTable,
)

MAX_IDENTIFIER_BYTES = 63
"""PostgreSQL NAMEDATALEN-1. 초과 이름은 해시 접미로 결정론적으로 줄인다."""

HASH_SUFFIX_CHARS = 8
"""이름 축소 접미의 sha256 16진수 자릿수(32비트)."""

INT4_MIN = -2_147_483_648
"""integer(4바이트) 하한. 관측 범위가 [하한, 상한] 안이면 INT, 아니면 BIGINT."""

INT4_MAX = 2_147_483_647
"""integer(4바이트) 상한."""


NUMERIC_PRECISION_MARGIN = 2
"""decimal → numeric(p, s)의 precision 여유 자릿수."""

NUMERIC_SCALE_MARGIN = 1
"""decimal → numeric(p, s)의 scale 여유 자릿수."""

NUMERIC_LIMIT = 38
"""계약(TargetColumn)·numeric 매핑의 정밀도 상한."""

VARCHAR_MARGIN = 20
"""string → varchar(n)의 관측 최대 길이 여유."""

VARCHAR_TEXT_LIMIT = 10_485_760
"""varchar 길이 상한(PostgreSQL). 여유를 더한 n이 이보다 크면 TEXT."""

TYPE_MAP: dict[PhysicalType, str] = {
    PhysicalType.BOOLEAN: "BOOLEAN",
    PhysicalType.DATE: "DATE",
    PhysicalType.TIME: "TIME",
    PhysicalType.TIMESTAMP: "TIMESTAMP",
    PhysicalType.TIMESTAMPTZ: "TIMESTAMPTZ",
    PhysicalType.JSON: "JSONB",
    PhysicalType.BINARY: "BYTEA",
    PhysicalType.FLOAT: "DOUBLE PRECISION",
}
"""관측값 없이 정해지는 물리 타입 → PostgreSQL 타입(동적 3종은 제외).

integer(범위에 따라 INTEGER/BIGINT)·decimal(numeric(p,s))·string(varchar(n)/
TEXT)은 _postgres_type이 관측값으로 정한다.
"""

_FK_SPEC = re.compile(r"^([a-z][a-z0-9_]*)\(([a-z][a-z0-9_]*)\)$")
"""계약의 FK 지정 '테이블(컬럼)' — 컬럼 하나(복합 FK는 같은 부모를 참조하는
컬럼 여러 개가 묶여 만든다)."""


def _quoted(name: str) -> exp.Identifier:
    """항상 인용된 식별자. 안의 큰따옴표는 sqlglot이 이중으로 이스케이프한다."""
    return exp.Identifier(this=name, quoted=True)


def _quoted_column(name: str) -> exp.Column:
    return exp.Column(this=_quoted(name))


def _shorten(name: str) -> str:
    """63바이트를 넘는 이름을 결정론적으로 줄인다(앞부분 + '_' + sha256 8자).

    UTF-8 바이트 기준으로 자른다(한글 1글자 = 3바이트) — 잘린 지점이 멀티바이트
    중간이면 앞 경계까지 당긴다.
    """
    budget = MAX_IDENTIFIER_BYTES - 1 - HASH_SUFFIX_CHARS  # 앞부분 허용 폭
    head = name.encode("utf-8")[:budget]
    while head:
        try:
            head.decode("utf-8")
            break
        except UnicodeDecodeError:  # 멀티바이트 중간이면 1바이트씩 당긴다
            head = head[:-1]
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:HASH_SUFFIX_CHARS]
    return head.decode("utf-8", errors="ignore") + "_" + digest


def _identifier_map(names: Sequence[str]) -> dict[str, str]:
    """이름 목록 전부에 안전한(63바이트 이내) 이름을 정한다.

    63바이트 이내 이름은 그대로, 초과 이름은 _shorten 결과. 서로 다른 이름이
    같은 안전 이름으로 겹치면 실패 닫힘(ValueError) — 해시 접미가 사실상
    막지만, 링크가 아니라 검사로 보장한다.
    """
    safe: dict[str, str] = {}
    used: dict[str, str] = {}
    for name in names:
        target = name if len(name.encode("utf-8")) <= MAX_IDENTIFIER_BYTES else _shorten(name)
        if target in used and used[target] != name:
            raise ValueError(f"식별자 축소 충돌: {target!r}({used[target]!r} vs {name!r})")
        used[target] = name
        safe[name] = target
    return safe


def _parse_foreign_key(spec: str) -> tuple[str, str]:
    """계약 FK 지정 '테이블(컬럼)'을 (테이블, 컬럼)으로 나눈다."""
    matched = _FK_SPEC.match(spec)
    if matched is None:
        raise ValueError(f"FK 지정 형식이 아니다(테이블(컬럼) 필요): {spec!r}")
    return matched.group(1), matched.group(2)


def _fk_references(
    table: TargetTable,
    parents: dict[str, TargetTable],
) -> list[tuple[list[str], str, list[str]]]:
    """테이블의 FK를 (자식 컬럼들, 참조 테이블, 참조 컬럼들) 목록으로.

    참조 대상은 부모의 PK여야 한다(이 생성기는 UNIQUE 제약을 만들지
    않는다 — PK가 아니면 PostgreSQL에서 FK가 성립하지 않는다). 부모 PK가
    단일 컬럼이면 자식 컬럼마다 FK를 따로 낸다 — 홈·원정 팀처럼 같은 부모를
    향하는 독립 FK를 하나의 복합 FK로 묶으면 성립하지 않는다. 부모 PK가
    복합이면 PK 컬럼 전부를 한 번씩 가리킬 때만 복합 FK 하나로 묶는다(자식
    컬럼 순서 = 테이블 정의 순서). 빠지거나 겹치면 실패 닫힘 — 계약의 FK
    지정은 컬럼 단위라 그 뜻을 알 수 없다. 자식과 부모의 물리 타입이 다르면
    실패 닫힘이다(성립하지 않는 FK를 만들지 않는다).
    """
    grouped: dict[str, list[tuple[str, str]]] = {}
    for column in table.columns:
        if column.foreign_key is None:
            continue
        parent_name, parent_column = _parse_foreign_key(column.foreign_key)
        parent_table = parents.get(parent_name)
        if parent_table is None:
            raise ValueError(f"FK 참조 대상이 후보에 없다: {table.name} → {parent_name}")
        parent_by_name = {c.name: c for c in parent_table.columns}
        target = parent_by_name.get(parent_column)
        if target is None:
            raise ValueError(f"FK 참조 컬럼이 부모에 없다: {parent_name}({parent_column})")
        if not target.is_primary_key:
            raise ValueError(f"FK 참조 컬럼이 부모 PK가 아니다: {parent_name}({parent_column})")
        if column.physical_type != target.physical_type:
            raise ValueError(
                f"FK 타입 불일치: {table.name}({column.name})은 "
                f"{parent_name}({parent_column})과 물리 타입이 다르다"
            )
        grouped.setdefault(parent_name, []).append((column.name, parent_column))
    references: list[tuple[list[str], str, list[str]]] = []
    for parent, pairs in grouped.items():
        parent_pk = [c.name for c in parents[parent].columns if c.is_primary_key]
        if len(parent_pk) == 1:
            for child, parent_column in pairs:  # 독립 FK — 묶지 않는다
                references.append(([child], parent, [parent_column]))
        elif sorted(pc for _, pc in pairs) == sorted(parent_pk):
            references.append(([child for child, _ in pairs], parent, [pc for _, pc in pairs]))
        else:
            raise ValueError(
                f"복합 FK가 부모 PK 전부를 한 번씩 가리키지 않는다: "
                f"{table.name} → {parent} PK {parent_pk}"
            )
    return references


def _postgres_type(
    column: TargetColumn,
    length_max: int | None,
    numeric_range: tuple[float, float] | None,
    *,
    full_scan: bool,
) -> exp.DataType:
    """물리 타입 → PostgreSQL 타입(동적 3종은 관측값으로 정한다).

    관측 한도는 전수일 때만 쓴다 — 표본 최댓값은 전수 최댓값보다 작을 수
    있어 VARCHAR(n)·INT 한도로 쓰면 적재가 깨진다(T-0025 '표본은 판정에만
    쓴다'와 같은 규칙).

    - integer: 전수 관측 범위가 int4 안이면 INT, 벗어나거나 범위 미상이거나
      관측이 표본이면 BIGINT(안전 쪽 — 데이터 손실 없음).
    - decimal: precision+2·scale+1 여유의 NUMERIC(p, s). 상한 38에 걸리면
      scale 여유를 먼저 포기해 정수부 자릿수(p-s)와 scale이 원래 값보다
      줄지 않게 한다(줄면 관측값이 적재 때 넘친다). precision이 없으면 무제한
      NUMERIC(정밀도를 모르는데 자릿수를 정하는 것보다 낫다).
    - string: 전수 관측 최대 길이+20의 VARCHAR(n). 관측이 표본이거나 길이를
      모르거나 상한을 넘으면 TEXT.
    """
    kind = column.physical_type
    if kind is PhysicalType.INTEGER:
        if (
            full_scan
            and numeric_range is not None
            and numeric_range[0] >= INT4_MIN
            and numeric_range[1] <= INT4_MAX
        ):
            return exp.DataType.build("INTEGER", dialect="postgres")
        return exp.DataType.build("BIGINT", dialect="postgres")
    if kind is PhysicalType.DECIMAL:
        if column.precision is None:
            return exp.DataType.build("NUMERIC", dialect="postgres")
        precision = min(column.precision + NUMERIC_PRECISION_MARGIN, NUMERIC_LIMIT)
        scale = column.scale or 0
        # 정수부 자릿수(precision-scale)는 여유보다 먼저 지킨다 — 상한에
        # 걸리면 scale 여유를 포기한다((38, 37)이 (38, 38) 정수부 0자리가
        # 되는 것 방지).
        scale = min(
            scale + NUMERIC_SCALE_MARGIN,
            precision - (column.precision - scale),
            precision,
        )
        return exp.DataType.build(f"NUMERIC({precision}, {scale})", dialect="postgres")
    if kind is PhysicalType.STRING:
        if not full_scan or length_max is None or length_max + VARCHAR_MARGIN > VARCHAR_TEXT_LIMIT:
            return exp.DataType.build("TEXT", dialect="postgres")
        return exp.DataType.build(f"VARCHAR({length_max + VARCHAR_MARGIN})", dialect="postgres")
    return exp.DataType.build(TYPE_MAP[kind], dialect="postgres")


def _topological_tables(tables: Sequence[TargetTable]) -> list[TargetTable]:
    """FK 의존 순서(부모가 먼저)로 테이블을 정렬한다(Kahn, 동률은 이름순).

    자기 참조는 무시한다(자기 테이블에 REFERENCES가 가능하다). 순환이 있으면
    실패 닫힘 — 순환 FK는 CREATE TABLE 안에서 성립하지 않는다(ADD CONSTRAINT
    과업이 아니면 불가).
    """
    by_name = {table.name: table for table in tables}
    if len(by_name) != len(tables):
        raise ValueError("후보에 같은 이름의 테이블이 있다")
    depends: dict[str, set[str]] = {
        table.name: {
            parent
            for _, parent, _ in _fk_references(table, by_name)
            if parent != table.name  # 자기 참조는 순서와 무관
        }
        for table in tables
    }
    ordered: list[TargetTable] = []
    done: set[str] = set()
    remaining = sorted(tables, key=lambda table: table.name)
    while remaining:
        ready = [table for table in remaining if depends[table.name] <= done]
        if not ready:
            names = sorted(table.name for table in remaining)
            raise ValueError(f"FK 순환이 있다: {names}")
        for table in ready:
            ordered.append(table)
            done.add(table.name)
        remaining = [table for table in remaining if table.name not in done]
    return ordered


def generate_ddl(
    candidate: SchemaCandidate,
    *,
    profiles: Sequence[ColumnProfile] = (),
    stats: Sequence[ColumnStats] = (),
    sampling: SamplingPlan | None = None,
    schema: str | None = None,
) -> str:
    """후보 스키마 → PostgreSQL CREATE TABLE 문(세미콜론·줄바꿈 구분).

    profiles·stats는 관측 기반 타입 매핑(string 길이·integer 범위)에만 쓴다.
    관측 한도는 SamplingPlan이 전수(full)일 때만 반영한다(표본이거나 계획이
    없으면 TEXT·BIGINT — 표본 최댓값을 한도로 쓰면 적재가 깨진다).
    TargetColumn.source_columns의 첫 컬럼(단일 원본) 기준으로 찾는다. 같은
    후보·프로파일·통계면 같은 바이트를 낸다.
    """
    full_scan = sampling is not None and sampling.strategy == "full"
    length_by_source = {profile.column: profile.length.maximum for profile in profiles}
    range_by_source: dict[str, tuple[float, float]] = {}
    for stat in stats:
        if stat.numeric is not None:
            range_by_source[stat.column] = (stat.numeric.minimum, stat.numeric.maximum)

    tables = _topological_tables(candidate.tables)
    parents = {table.name: table for table in tables}
    # 컬럼별 PostgreSQL 타입 — FK 자식 컬럼은 부모 컬럼과 같은 타입으로 낸다.
    type_by_key: dict[tuple[str, str], exp.DataType] = {}
    for table in tables:
        for column in table.columns:
            source = column.source_columns[0]
            type_by_key[(table.name, column.name)] = _postgres_type(
                column,
                length_by_source.get(source),
                range_by_source.get(source),
                full_scan=full_scan,
            )
    # 식별자 안전 맵: 테이블(스키마 포함)·컬럼 전부에서 유일해야 한다.
    all_names: list[str] = [table.name for table in tables]
    for table in tables:
        all_names.extend(column.name for column in table.columns)
        for _, parent, parent_columns in _fk_references(table, parents):
            all_names.append(parent)
            all_names.extend(parent_columns)
    if schema is not None and len(schema.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
        raise ValueError("스키마 이름이 63바이트를 넘는다")
    safe = _identifier_map(all_names)

    statements: list[str] = []
    for table in tables:
        column_defs: list[exp.Expression] = []
        seen_columns: set[str] = set()
        for column in table.columns:
            if column.name in seen_columns:
                raise ValueError(f"테이블 {table.name}에 같은 이름의 컬럼이 있다: {column.name}")
            seen_columns.add(column.name)
            kind = type_by_key[(table.name, column.name)]
            if column.foreign_key is not None:  # 부모와 같은 타입(관측이 섞이지 않게)
                parent_name, parent_column = _parse_foreign_key(column.foreign_key)
                kind = type_by_key[(parent_name, parent_column)]
            constraints = []
            if not column.nullable:
                constraints.append(exp.NotNullColumnConstraint())
            column_defs.append(
                exp.ColumnDef(
                    this=_quoted_column(safe[column.name]),
                    kind=kind,
                    constraints=constraints,
                )
            )
        pk_columns = [column.name for column in table.columns if column.is_primary_key]
        if pk_columns:
            column_defs.append(
                exp.PrimaryKey(expressions=[_quoted_column(safe[name]) for name in pk_columns])
            )
        for child_columns, parent, parent_columns in _fk_references(table, parents):
            reference_table = exp.Table(this=_quoted(safe[parent]))
            if schema is not None:  # 참조도 같은 스키마로 한정(search_path 오남용 방지)
                reference_table = exp.Table(this=reference_table.this, db=_quoted(schema))
            column_defs.append(
                exp.ForeignKey(
                    expressions=[_quoted_column(safe[name]) for name in child_columns],
                    reference=exp.Reference(
                        this=reference_table,
                        expressions=[_quoted_column(safe[name]) for name in parent_columns],
                    ),
                )
            )
        target = exp.Table(this=_quoted(safe[table.name]))
        if schema is not None:
            target = exp.Table(this=target.this, db=_quoted(schema))
        create = exp.Create(this=exp.Schema(this=target, expressions=column_defs), kind="TABLE")
        statements.append(create.sql(dialect="postgres", identify=True))
    return ";\n".join(statements) + (";" if statements else "")


def verify_ddl(
    ddl: str,
    candidate: SchemaCandidate,
    *,
    schema: str | None = None,
) -> list[dict[str, object]]:
    """생성한 DDL을 sqlglot으로 다시 파싱해 후보와 대조한다(실패 닫힘).

    문장 수(테이블 수), 테이블·컬럼 이름(축소 맵 적용), PK 컬럼, FK(자식 →
    참조, 스키마 한정 포함), 컬럼별 NOT NULL이 하나라도 어긋나면 ValueError.
    PK 컬럼은 PostgreSQL이 NOT NULL로 다루므로 명시 여부는 물지 않는다.
    통과하면 테이블별 요약을 돌려준다(S10 검증이 쓴다).
    """
    statements = [stmt for stmt in sqlglot_parse(ddl, read="postgres") if stmt is not None]
    expected_tables = _topological_tables(candidate.tables)
    parents = {table.name: table for table in expected_tables}
    all_names: list[str] = [table.name for table in expected_tables]
    for table in expected_tables:
        all_names.extend(column.name for column in table.columns)
        for _, parent, parent_columns in _fk_references(table, parents):
            all_names.append(parent)
            all_names.extend(parent_columns)
    safe = _identifier_map(all_names)

    creates = [stmt for stmt in statements if isinstance(stmt, exp.Create)]
    if len(statements) != len(expected_tables) or len(creates) != len(expected_tables):
        raise ValueError(
            f"DDL 문장 수 불일치: {len(statements)}(CREATE {len(creates)})"
            f" vs 테이블 {len(expected_tables)}"
        )
    summaries: list[dict[str, object]] = []
    for table, create in zip(expected_tables, creates, strict=True):
        create_schema = create.this
        if not isinstance(create_schema, exp.Schema):
            raise ValueError(f"CREATE가 스키마 정의가 아니다: {table.name}")
        target = create_schema.this
        if schema is not None and target.db != schema:
            raise ValueError(f"스키마 한정 불일치: {target.db!r}")
        if target.name != safe[table.name]:
            raise ValueError(f"테이블 이름 불일치: {target.name!r} vs {safe[table.name]!r}")
        column_defs = [e for e in create_schema.expressions if isinstance(e, exp.ColumnDef)]
        expected_columns = [safe[column.name] for column in table.columns]
        actual_columns = [column_def.name for column_def in column_defs]
        if actual_columns != expected_columns:
            raise ValueError(f"컬럼 불일치({table.name}): {actual_columns} vs {expected_columns}")
        for column, column_def in zip(table.columns, column_defs, strict=True):
            if column.is_primary_key:
                continue  # PK는 PostgreSQL이 NOT NULL로 다룬다(명시 여부 자유)
            actual_not_null = column_def.find(exp.NotNullColumnConstraint) is not None
            if actual_not_null != (not column.nullable):
                raise ValueError(
                    f"NOT NULL 불일치({table.name}.{column_def.name}): "
                    f"{'있음' if actual_not_null else '없음'} vs nullable={column.nullable}"
                )
        pk = next((e for e in create_schema.expressions if isinstance(e, exp.PrimaryKey)), None)
        expected_pk = [safe[c.name] for c in table.columns if c.is_primary_key]
        actual_pk = [c.name for c in pk.expressions] if pk is not None else []
        if actual_pk != expected_pk:
            raise ValueError(f"PK 불일치({table.name}): {actual_pk} vs {expected_pk}")
        actual_fks: list[tuple[list[str], str, list[str]]] = []
        for e in create_schema.expressions:
            if not isinstance(e, exp.ForeignKey):
                continue
            reference = e.args["reference"]
            parent_schema = reference.this
            parent_table = parent_schema.this
            if schema is not None and parent_table.db != schema:
                raise ValueError(f"FK 스키마 한정 불일치: {parent_table.db!r} vs {schema!r}")
            actual_fks.append(
                (
                    [c.name for c in e.expressions],
                    parent_table.name,
                    [c.name for c in parent_schema.expressions],
                )
            )
        expected_fks = [
            (
                [safe[name] for name in child_columns],
                safe[parent],
                [safe[name] for name in parent_columns],
            )
            for child_columns, parent, parent_columns in _fk_references(table, parents)
        ]
        if sorted(actual_fks) != sorted(expected_fks):
            raise ValueError(f"FK 불일치({table.name}): {actual_fks} vs {expected_fks}")
        summaries.append(
            {
                "table": safe[table.name],
                "columns": len(column_defs),
                "primary_key": actual_pk,
                "foreign_keys": len(actual_fks),
            }
        )
    return summaries
