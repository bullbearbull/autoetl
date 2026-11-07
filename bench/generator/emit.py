"""결정론적 데이터 생성, 평면화, 정답·메니페스트 출력."""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from bench.answer import (
    PHYSICAL_TYPE_OF_KIND,
    AnswerColumn,
    AnswerForeignKey,
    AnswerFunctionalDependency,
    AnswerTable,
    BenchAnswer,
)
from bench.generator.packs import ColumnSpec, FlatColumn, Pack

KOREAN_FAMILY = ("김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오")
KOREAN_GIVEN = (
    "민준",
    "서연",
    "도윤",
    "지우",
    "예준",
    "하은",
    "시우",
    "서현",
    "지호",
    "유나",
    "준서",
    "수아",
    "현우",
    "윤서",
    "지안",
)
CITIES = ("서울", "부산", "인천", "대구", "대전", "광주", "수원", "성남", "용인", "고양")
ORDER_STATUS = ("결제완료", "배송중", "배송완료", "취소", "반품")
CHANNELS = ("앱", "웹", "매장", "전화")
PRODUCT_CATEGORIES = ("식품", "의류", "가전", "도서", "생활", "스포츠")
PRODUCT_WORDS = ("프리미엄", "실속", "친환경", "미니", "프로", "클래식", "신상")
COUNTRIES = ("한국", "미국", "영국", "일본", "프랑스", "브라질")
GENRES = ("발라드", "댄스", "록", "재즈", "클래식", "힙합", "포크")
TITLE_WORDS = ("봄", "여름", "가을", "겨울", "밤", "아침", "바다", "도시", "꿈", "기억")

CATEGORY_POOLS: dict[tuple[str, str], tuple[str, ...]] = {
    ("customers", "city"): CITIES,
    ("orders", "status"): ORDER_STATUS,
    ("orders", "channel"): CHANNELS,
    ("products", "category"): PRODUCT_CATEGORIES,
    ("artists", "country"): COUNTRIES,
    ("albums", "genre"): GENRES,
}


def _value(kind: str, rng: random.Random, spec: ColumnSpec, seq: int, table_name: str) -> str:
    """컬럼 종류에 따라 결정론적으로 값을 하나 만든다."""
    if kind == "id":
        return str(seq + 1)
    if kind == "int":
        return str(rng.randint(1, 100))
    if kind == "amount":
        return str(rng.randrange(900, 9_999_900, 100))
    if kind == "date":
        start = date(2024, 1, 1)
        return (start + timedelta(days=rng.randint(0, 729))).isoformat()
    if kind == "code":
        return str(rng.randint(0, 10**spec.zero_pad - 1)).zfill(spec.zero_pad)
    if kind == "name_kr":
        return rng.choice(KOREAN_FAMILY) + rng.choice(KOREAN_GIVEN)
    if kind == "email":
        return f"user{seq + 1}@{rng.choice(('example.com', 'example.co.kr'))}"
    if kind == "phone":
        return f"010-{rng.randint(2000, 9999)}-{rng.randint(1000, 9999)}"
    if kind == "category":
        return rng.choice(CATEGORY_POOLS[(table_name, spec.name)])
    if kind == "text":
        if table_name == "products":
            return f"{rng.choice(PRODUCT_WORDS)} {rng.choice(PRODUCT_CATEGORIES)} {seq + 1}호"
        if table_name == "albums":
            return f"{rng.choice(TITLE_WORDS)}의 {rng.choice(TITLE_WORDS)}"
        return f"{rng.choice(TITLE_WORDS)} {seq + 1}"
    raise ValueError(f"알 수 없는 컬럼 종류: {kind}")


def generate_pack(pack: Pack, seed: int) -> dict[str, list[dict[str, str]]]:
    """팩 전체 테이블을 FK 의존 순서로 생성한다. 같은 시드 → 같은 결과."""
    tables: dict[str, list[dict[str, str]]] = {}
    for table_name in _dependency_order(pack):
        table = pack.table(table_name)
        rng = random.Random(f"{pack.name}:{table_name}:{seed}")
        rows: list[dict[str, str]] = []
        for seq in range(table.rows):
            row: dict[str, str] = {}
            for spec in table.columns:
                if spec.kind == "fk":
                    parent_name, parent_column = (spec.ref or "").split(".")
                    parent_rows = tables[parent_name]
                    row[spec.name] = rng.choice(parent_rows)[parent_column]
                else:
                    row[spec.name] = _value(spec.kind, rng, spec, seq, table_name)
            rows.append(row)
        tables[table_name] = rows
    return tables


def flat_layout(pack: Pack) -> list[FlatColumn]:
    """평면 파일의 컬럼 배치(컬럼 ID 순서)를 팩 선언에서 만든다.

    컬럼 ID는 테이블 접두사가 없는 이름이다. FK 컬럼은 참조 대상 부모 PK와 같은
    평면 컬럼을 가리키므로 조인 키는 처음 나오는 위치에 한 번만 놓인다(부모가
    선언 뒤에 와도 자식 FK 위치가 첫 등장이 된다). 조인 키가 아닌 컬럼 이름이
    팩 안에서 겹치면 헤더가 유일해질 수 없으므로 오류를 낸다. 부모 PK와 다른
    이름의 FK 참조도 지원하지 않는다(아래 참조).
    """
    placed: dict[str, FlatColumn] = {}
    order: list[FlatColumn] = []
    for table in pack.tables:
        for column in table.columns:
            column_id = (column.ref or "").split(".")[1] if column.kind == "fk" else column.name
            if column.kind == "fk" and column.name != column_id:
                # 같은 부모 PK를 다른 이름으로 참조하는 FK(예: billing_customer_id →
                # customers.customer_id)는 둘 다 같은 컬럼 ID로 합쳐져 조용히 사라진다.
                # v0 평면화는 부모 PK와 같은 이름의 조인 키만 지원한다.
                raise ValueError(
                    f"이름이 다른 FK 참조는 v0 평면화에서 지원하지 않는다: "
                    f"{table.name}.{column.name} → {column.ref}. "
                    "부모 PK와 같은 이름을 쓰거나 팩 구조를 바꿔야 한다"
                )
            existing = placed.get(column_id)
            if existing is not None:
                if column.kind == "fk":
                    continue  # 조인 키: 부모(또는 먼저 온 FK) 위치에 이미 있다
                if (
                    column.pk
                    and existing.kind == "fk"
                    and existing.ref_of == f"{table.name}.{column.name}"
                ):
                    continue  # 늦게 도착한 부모 PK: 자식 FK가 자리를 잡았다
                raise ValueError(
                    f"평면 컬럼 이름 충돌: '{column_id}'({table.name}.{column.name})이(가) "
                    f"{existing.table}.{existing.name}과(와) 겹치는데 조인 키 관계가 아니다. "
                    "팩 정의에서 이름을 다르게 써야 한다"
                )
            entry = FlatColumn(
                column_id=column_id,
                kind=column.kind,
                is_key=column.pk or column.kind == "fk",
                table=table.name,
                name=column.name,
                ref_of=column.ref or "",
            )
            placed[column_id] = entry
            order.append(entry)
    return order


def _dependency_order(pack: Pack) -> list[str]:
    remaining = {table.name for table in pack.tables}
    order: list[str] = []
    while remaining:
        ready = [
            name
            for name in sorted(remaining)
            if {
                (column.ref or "").split(".")[0]
                for column in pack.table(name).columns
                if column.kind == "fk"
            }
            <= set(order)
        ]
        if not ready:
            raise ValueError("FK 순환이 있거나 없는 테이블을 참조한다")
        order.extend(ready)
        remaining -= set(ready)
    return order


def flatten(
    pack: Pack, tables: dict[str, list[dict[str, str]]]
) -> tuple[list[str], list[list[str]]]:
    """리프 테이블에서 FK를 따라가며 모든 테이블을 조인해 평면 테이블을 만든다.

    부모 체인(leaf → root)뿐 아니라 체인 밖 차원 테이블(예: 주문상품→상품)도
    FK로 참조되면 모두 펼친다. 정답 복원 벤치는 평면 파일에 모든 원본 컬럼이
    있어야 성립하므로, 팩의 모든 테이블이 조인되지 않으면 오류를 낸다.
    헤더는 flat_layout의 컬럼 ID(접두사 없는 이름)를 그대로 쓰고, 조인 키는
    한 번만 나온다. v0는 단일 컬럼 PK/FK만 지원한다.
    """
    pk_of = {table.name: table.pk[0] for table in pack.tables}
    index = {
        table.name: {row[pk_of[table.name]]: row for row in tables[table.name]}
        for table in pack.tables
    }
    fk_columns = {
        table.name: {
            (column.ref or "").split(".")[0]: column.name
            for column in table.columns
            if column.kind == "fk"
        }
        for table in pack.tables
    }
    layout = flat_layout(pack)
    header = [column.column_id for column in layout]
    all_names = {table.name for table in pack.tables}
    flat_rows: list[list[str]] = []
    for leaf_row in tables[pack.leaf]:
        merged: dict[str, dict[str, str]] = {pack.leaf: leaf_row}
        pending = [pack.leaf]
        while pending:
            current = pending.pop()
            current_row = merged[current]
            for parent, fk_name in fk_columns[current].items():
                if parent in merged:
                    continue
                merged[parent] = index[parent][current_row[fk_name]]
                pending.append(parent)
        if set(merged) != all_names:
            missing = all_names - set(merged)
            raise ValueError(f"리프에서 닿지 않는 테이블: {sorted(missing)}")
        flat_rows.append([merged[column.table][column.name] for column in layout])
    return header, flat_rows


def answer_schema(
    pack: Pack,
    tables: dict[str, list[dict[str, str]]],
    *,
    variant_headers: dict[str, list[str]],
    variant_columns: dict[str, list[str]],
) -> BenchAnswer:
    """정답 스키마. 형식 정의는 bench/answer.py(BenchAnswer)를 따른다.

    FK 컬럼의 column_id는 참조 대상 부모 PK의 컬럼 ID다(자식 FK와 부모 PK는
    같은 평면 컬럼을 가리킨다). variant_headers(변형별 필드 헤더 행)와
    variant_columns(그 변형 CSV 컬럼 순서의 컬럼 ID)이 위치가 짝을 이룬다 —
    채점의 이름 → 컬럼 ID 대응표다(T-0037, hard는 컬럼 순서를 섞는다).
    """
    answer_tables: list[AnswerTable] = []
    for table in pack.tables:
        primary_key = table.pk
        foreign_keys = [
            AnswerForeignKey(
                table=table.name,
                columns=[column.name],
                references_table=(column.ref or "").split(".")[0],
                references_columns=[(column.ref or "").split(".")[1]],
            )
            for column in table.columns
            if column.kind == "fk"
        ]
        functional_dependencies = [
            AnswerFunctionalDependency(
                table=table.name, determinant=primary_key, dependent=column.name
            )
            for column in table.columns
            if column.name not in primary_key
        ]
        answer_tables.append(
            AnswerTable(
                name=table.name,
                primary_key=primary_key,
                columns=[
                    AnswerColumn(
                        name=column.name,
                        column_id=(
                            (column.ref or "").split(".")[1] if column.kind == "fk" else column.name
                        ),
                        physical_type=PHYSICAL_TYPE_OF_KIND[column.kind],
                        is_foreign_key=column.kind == "fk",
                    )
                    for column in table.columns
                ],
                foreign_keys=foreign_keys,
                functional_dependencies=functional_dependencies,
                row_count=len(tables[table.name]),
            )
        )
    return BenchAnswer(
        pack=pack.name,
        tables=answer_tables,
        variant_headers=variant_headers,
        variant_columns=variant_columns,
    )


def write_csv(
    path: Path, header: list[str], rows: list[list[str]], *, encoding: str = "utf-8"
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
