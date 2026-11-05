"""벤치용 합성 관계형 스키마 팩.

각 팩은 정답 스키마(테이블·PK·FK)를 알고 있는 관계형 구조를 선언하고, 값 생성기가
시드에 따라 결정론적으로 행을 채운다. 조인해 평면화하면 '역정규화 후 복원' 벤치의
입력이 되고, 팩 선언 자체가 정답(answer.json)이 된다.

라이선스: 모든 팩은 직접 생성한 합성 데이터다(외부 저작물 미포함, 제약 없음).
실제 공개 DB(Chinook·Northwind·Sakila) 원본을 받아 쓰는 수입기는 데이터별 이용
조건 확인(소유자 검토) 뒤 v1에서 추가한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ColumnSpec:
    """테이블 컬럼 하나. kind는 값 생성 방식을 정한다."""

    name: str
    kind: str  # id | fk | int | amount | date | code | name_kr | email | phone | category | text
    pk: bool = False
    ref: str | None = None  # fk일 때 "테이블.컬럼"
    zero_pad: int = 0  # code의 고정 자릿수(앞자리 0 보존 함정용)


@dataclass(frozen=True)
class TableSpec:
    name: str
    rows: int
    columns: tuple[ColumnSpec, ...]
    parent: str | None = field(default=None)
    """부모 테이블 이름. 평면화는 이 연결(leaf → root)을 따라 조인한다."""

    @property
    def pk(self) -> list[str]:
        return [column.name for column in self.columns if column.pk]


@dataclass(frozen=True)
class Pack:
    name: str
    description: str
    license: str
    root: str  # 조인 체인의 루트(1쪽) 테이블
    leaf: str  # 조인 체인의 리프(N쪽) 테이블 = 평면화의 기준
    tables: tuple[TableSpec, ...]

    def table(self, name: str) -> TableSpec:
        for table in self.tables:
            if table.name == name:
                return table
        raise KeyError(name)

    def join_chain(self) -> list[TableSpec]:
        """리프부터 루트까지 조인 체인을 돌려준다."""
        chain = [self.table(self.leaf)]
        while chain[-1].parent is not None:
            chain.append(self.table(chain[-1].parent))
        if chain[-1].name != self.root:
            raise ValueError(f"조인 체인이 루트({self.root})에 닿지 않는다")
        return chain


ORDERS_KR = Pack(
    name="orders_kr",
    description="한국 전자상거래 주문(고객→주문→주문상품→상품)",
    license="합성 데이터(직접 생성, 외부 저작물 없음, 제약 없음)",
    root="customers",
    leaf="order_items",
    tables=(
        TableSpec(
            name="customers",
            rows=60,
            columns=(
                ColumnSpec(name="customer_id", kind="id", pk=True),
                ColumnSpec(name="customer_name", kind="name_kr"),
                ColumnSpec(name="email", kind="email"),
                ColumnSpec(name="phone", kind="phone"),
                ColumnSpec(name="city", kind="category"),
                ColumnSpec(name="signup_date", kind="date"),
            ),
        ),
        TableSpec(
            name="orders",
            rows=300,
            parent="customers",
            columns=(
                ColumnSpec(name="order_id", kind="id", pk=True),
                ColumnSpec(name="order_date", kind="date"),
                ColumnSpec(name="status", kind="category"),
                ColumnSpec(name="channel", kind="category"),
                ColumnSpec(name="customer_id", kind="fk", ref="customers.customer_id"),
            ),
        ),
        TableSpec(
            name="order_items",
            rows=900,
            parent="orders",
            columns=(
                ColumnSpec(name="order_item_id", kind="id", pk=True),
                ColumnSpec(name="quantity", kind="int"),
                ColumnSpec(name="unit_price", kind="amount"),
                ColumnSpec(name="discount_rate", kind="int"),
                ColumnSpec(name="product_id", kind="fk", ref="products.product_id"),
                ColumnSpec(name="order_id", kind="fk", ref="orders.order_id"),
            ),
        ),
        TableSpec(
            name="products",
            rows=120,
            columns=(
                ColumnSpec(name="product_id", kind="id", pk=True),
                ColumnSpec(name="product_code", kind="code", zero_pad=6),
                ColumnSpec(name="product_name", kind="text"),
                ColumnSpec(name="category", kind="category"),
                ColumnSpec(name="list_price", kind="amount"),
            ),
        ),
    ),
)

MUSIC = Pack(
    name="music",
    description="공개 음원 DB 유형 구조(아티스트→앨범→트랙, 합성 데이터)",
    license="합성 데이터(직접 생성, 외부 저작물 없음, 제약 없음)",
    root="artists",
    leaf="tracks",
    tables=(
        TableSpec(
            name="artists",
            rows=40,
            columns=(
                ColumnSpec(name="artist_id", kind="id", pk=True),
                ColumnSpec(name="artist_name", kind="text"),
                ColumnSpec(name="country", kind="category"),
                ColumnSpec(name="debut_year", kind="int"),
            ),
        ),
        TableSpec(
            name="albums",
            rows=160,
            parent="artists",
            columns=(
                ColumnSpec(name="album_id", kind="id", pk=True),
                ColumnSpec(name="title", kind="text"),
                ColumnSpec(name="release_date", kind="date"),
                ColumnSpec(name="genre", kind="category"),
                ColumnSpec(name="artist_id", kind="fk", ref="artists.artist_id"),
            ),
        ),
        TableSpec(
            name="tracks",
            rows=800,
            parent="albums",
            columns=(
                ColumnSpec(name="track_id", kind="id", pk=True),
                ColumnSpec(name="track_name", kind="text"),
                ColumnSpec(name="duration_seconds", kind="int"),
                ColumnSpec(name="track_no", kind="int"),
                ColumnSpec(name="album_id", kind="fk", ref="albums.album_id"),
            ),
        ),
    ),
)

PACKS: dict[str, Pack] = {pack.name: pack for pack in (ORDERS_KR, MUSIC)}
"""사용 가능한 팩. 이름 → 팩."""
