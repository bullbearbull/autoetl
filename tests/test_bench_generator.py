"""벤치 데이터셋 생성기 v0 검증(T-0015: 접두사 없는 헤더·컬럼 ID·kind 기반 잡음)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from bench.answer import ANSWER_VERSION, BenchAnswer
from bench.generator import PACKS, build_dataset, flat_layout, flatten, generate_pack
from bench.generator.noise import GROUP_LABELS, NULL_TOKENS, VARIANTS, apply_noise
from bench.generator.packs import ColumnSpec, FlatColumn, Pack, TableSpec

EXPECTED_COLUMNS = {"orders_kr": 19, "music": 13}
"""조인 키를 한 번만 내보낼 때의 평면 컬럼 수(catalog_no 추가 기준)."""


def _dataset(pack_name: str, tmp_path: Path) -> Path:
    out = tmp_path / f"{pack_name}-42"
    build_dataset(PACKS[pack_name], seed=42, out_dir=out)
    return out


def _read_csv(path: Path, encoding: str = "utf-8") -> list[list[str]]:
    with path.open(encoding=encoding, newline="") as handle:
        return list(csv.reader(handle))


# ── 팩·생성 결정론 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_generation_is_deterministic(pack_name: str) -> None:
    pack = PACKS[pack_name]
    first = generate_pack(pack, seed=42)
    second = generate_pack(pack, seed=42)
    assert first == second
    other = generate_pack(pack, seed=43)
    assert first != other


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_same_seed_same_bytes(pack_name: str, tmp_path: Path) -> None:
    first = _dataset(pack_name, tmp_path / "a")
    second = _dataset(pack_name, tmp_path / "b")
    for variant in VARIANTS:
        name = f"{variant}.csv"
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
    assert (first / "answer.json").read_bytes() == (second / "answer.json").read_bytes()


def test_row_counts_match_spec() -> None:
    for pack in PACKS.values():
        tables = generate_pack(pack, seed=1)
        for table in pack.tables:
            assert len(tables[table.name]) == table.rows


def test_fk_values_reference_parents() -> None:
    for pack in PACKS.values():
        tables = generate_pack(pack, seed=7)
        for table in pack.tables:
            for column in table.columns:
                if column.kind != "fk":
                    continue
                parent_name, parent_column = (column.ref or "").split(".")
                parent_keys = {row[parent_column] for row in tables[parent_name]}
                assert {row[column.name] for row in tables[table.name]} <= parent_keys


# ── 평면 배치: 접두사 없는 헤더, 조인 키 1회 ────────────────────────────────


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_flat_header_has_no_table_prefix_and_unique_names(pack_name: str) -> None:
    pack = PACKS[pack_name]
    header, _rows = flatten(pack, generate_pack(pack, seed=7))
    assert len(header) == len(set(header)), "평면 헤더는 유일해야 한다"
    assert len(header) == EXPECTED_COLUMNS[pack_name]
    for table in pack.tables:
        for column in table.columns:
            if column.kind == "fk":
                continue  # FK 이름은 부모 PK와 같아 예외 대상이 아니다(아래 테스트)
            assert column.name in header


def test_join_keys_appear_once() -> None:
    for pack in PACKS.values():
        layout = flat_layout(pack)
        header = [column.column_id for column in layout]
        for table in pack.tables:
            for column in table.columns:
                if column.pk:
                    assert header.count(column.name) == 1, f"조인 키 {column.name}은 한 번"


def test_layout_marks_keys_and_kinds() -> None:
    layout = flat_layout(PACKS["orders_kr"])
    by_id = {column.column_id: column for column in layout}
    assert by_id["customer_id"].is_key  # customers PK이자 orders FK
    assert by_id["order_id"].is_key
    assert by_id["product_id"].is_key  # 자식(order_items) 위치에 먼저 놓여도 키
    assert by_id["product_id"].kind == "fk"  # 첫 등장은 order_items의 FK 위치
    assert not by_id["quantity"].is_key
    assert by_id["unit_price"].kind == "amount"
    assert by_id["order_date"].kind == "date"


def test_flatten_keeps_leaf_row_count_and_unique_ids() -> None:
    for pack in PACKS.values():
        tables = generate_pack(pack, seed=7)
        header, rows = flatten(pack, tables)
        assert len(rows) == len(tables[pack.leaf])
        leaf_pk = pack.table(pack.leaf).pk[0]
        id_column = header.index(leaf_pk)
        ids = [row[id_column] for row in rows]
        assert len(ids) == len(set(ids)), "리프 PK는 평면 테이블에서도 유일해야 한다"
        assert len(header) == len(flat_layout(pack))


def test_layout_rejects_non_join_name_collision() -> None:
    bad = Pack(
        name="bad",
        description="같은 이름의 비키 컬럼이 두 테이블에 있다",
        license="합성",
        root="a",
        leaf="b",
        tables=(
            TableSpec(
                name="a",
                rows=2,
                columns=(
                    ColumnSpec(name="a_id", kind="id", pk=True),
                    ColumnSpec(name="memo", kind="text"),
                ),
            ),
            TableSpec(
                name="b",
                rows=4,
                parent="a",
                columns=(
                    ColumnSpec(name="b_id", kind="id", pk=True),
                    ColumnSpec(name="memo", kind="text"),  # a.memo과 충돌
                    ColumnSpec(name="a_id", kind="fk", ref="a.a_id"),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="이름 충돌"):
        flat_layout(bad)


def test_layout_rejects_differently_named_fk_to_same_parent() -> None:
    # Codex 지적(소유자 반영 요청 3): billing_customer_id·shipping_customer_id처럼
    # 같은 부모 PK를 다른 이름으로 참조하면 한 평면 컬럼으로 조용히 합쳐진다.
    aliased = Pack(
        name="aliased",
        description="같은 부모를 다른 이름으로 참조하는 FK가 있다",
        license="합성",
        root="customers",
        leaf="orders",
        tables=(
            TableSpec(
                name="customers",
                rows=5,
                columns=(ColumnSpec(name="customer_id", kind="id", pk=True),),
            ),
            TableSpec(
                name="orders",
                rows=10,
                parent="customers",
                columns=(
                    ColumnSpec(name="order_id", kind="id", pk=True),
                    ColumnSpec(name="billing_customer_id", kind="fk", ref="customers.customer_id"),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="이름이 다른 FK 참조"):
        flat_layout(aliased)


# ── 잡음 변형 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_missing_variant_spares_keys(pack_name: str) -> None:
    pack = PACKS[pack_name]
    header, rows = flatten(pack, generate_pack(pack, seed=7))
    layout = flat_layout(pack)
    noisy, _encoding = apply_noise(
        "missing", header, rows, seed=7, columns=layout, missing_rate=0.3
    )
    body = noisy[1:]
    tokens = set(NULL_TOKENS)
    key_indexes = [i for i, column in enumerate(layout) if column.is_key]
    other_indexes = [i for i, column in enumerate(layout) if not column.is_key]
    for i in key_indexes:
        assert all(row[i] not in tokens for row in body), "키 컬럼에는 결측이 없어야 한다"
    assert any(row[i] in tokens for row in body for i in other_indexes), (
        "키가 아닌 컬럼에는 결측이 들어가야 한다"
    )


def test_format_mix_produces_date_and_amount_variants() -> None:
    pack = PACKS["orders_kr"]
    header, rows = flatten(pack, generate_pack(pack, seed=7))
    layout = flat_layout(pack)
    noisy, _encoding = apply_noise("format_mix", header, rows, seed=7, columns=layout)
    kinds = [column.kind for column in layout]
    date_index = kinds.index("date")
    amount_index = kinds.index("amount")
    date_values = {row[date_index] for row in noisy[1:]}
    amount_values = {row[amount_index] for row in noisy[1:]}
    assert any("-" not in value for value in date_values), "yyyy mmdd·시리얼 변형 필요"
    assert any(("," in value or "원" in value) for value in amount_values)


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_leading_zero_variant_differs_in_both_packs(pack_name: str) -> None:
    pack = PACKS[pack_name]
    header, rows = flatten(pack, generate_pack(pack, seed=7))
    layout = flat_layout(pack)
    kinds = [column.kind for column in layout]
    code_index = kinds.index("code")
    clean_codes = [row[code_index] for row in rows]
    assert any(code.startswith("0") for code in clean_codes), "원본에 앞자리 0이 있어야 한다"
    noisy, _encoding = apply_noise("leading_zero", header, rows, seed=7, columns=layout)
    noisy_codes = [row[code_index] for row in noisy[1:]]
    assert noisy_codes != clean_codes, "두 팩 모두 leading_zero는 clean과 달라야 한다"


def test_multi_header_group_row_hides_table_assignment() -> None:
    pack = PACKS["orders_kr"]
    header, rows = flatten(pack, generate_pack(pack, seed=7))
    layout = flat_layout(pack)
    noisy, _encoding = apply_noise("multi_header", header, rows, seed=7, columns=layout)
    group_row, field_row = noisy[0], noisy[1]
    assert field_row == header
    assert len(group_row) == len(header)
    table_names = [table.name for table in pack.tables]
    assert all(name not in cell for name in table_names for cell in group_row + field_row), (
        "그룹 행·필드 행에 정답 테이블 이름이 나오면 안 된다"
    )
    assert all(" | " not in cell for cell in group_row), "깨진 조합 문자열이 없어야 한다"
    assert set(group_row) <= set(GROUP_LABELS.values()), "그룹 라벨은 컬럼 성격만 쓴다"


def test_hard_variant_uses_nameless_field_row() -> None:
    pack = PACKS["music"]
    header, rows = flatten(pack, generate_pack(pack, seed=1))
    layout = flat_layout(pack)
    noisy, encoding = apply_noise("hard", header, rows, seed=1, columns=layout)
    assert encoding == "cp949"
    group_row, field_row = noisy[0], noisy[1]
    assert all(cell.startswith("col_") for cell in field_row), "hard 필드 행은 col_01 형식"
    assert field_row == [f"col_{i:02d}" for i in range(1, len(header) + 1)]
    assert len(group_row) == len(header)


def test_cp949_variant_round_trips(tmp_path: Path) -> None:
    pack = PACKS["orders_kr"]
    header, rows = flatten(pack, generate_pack(pack, seed=7))
    layout = flat_layout(pack)
    cp949_rows, encoding = apply_noise("cp949", header, rows, seed=7, columns=layout)
    from bench.generator.emit import write_csv

    path = tmp_path / "cp949.csv"
    write_csv(path, cp949_rows[0], cp949_rows[1:], encoding=encoding)
    assert encoding == "cp949"
    content = path.read_text(encoding="cp949")
    assert "결제완료" in content or "고객" in content


# ── 데이터셋 산출물 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_build_dataset_writes_complete_artifacts(pack_name: str, tmp_path: Path) -> None:
    pack = PACKS[pack_name]
    out_dir = tmp_path / f"{pack.name}-42"
    manifest = build_dataset(pack, seed=42, out_dir=out_dir)

    assert (out_dir / "README.md").exists()
    assert (out_dir / "answer.json").exists()
    assert (out_dir / "manifest.json").exists()
    assert [item["variant"] for item in manifest["files"]] == list(VARIANTS)
    assert manifest["columns"] == EXPECTED_COLUMNS[pack_name]

    clean = _read_csv(out_dir / "clean.csv")
    assert len(clean) - 1 == pack.table(pack.leaf).rows
    hard = _read_csv(out_dir / "hard.csv", encoding="cp949")
    assert len(hard) - 2 == len(clean) - 1, "hard는 헤더가 2행(그룹·필드)이다"


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_no_variant_header_leaks_table_names(pack_name: str, tmp_path: Path) -> None:
    out = _dataset(pack_name, tmp_path)
    answer = BenchAnswer.model_validate_json((out / "answer.json").read_text(encoding="utf-8"))
    assert set(answer.variant_headers) == set(VARIANTS)
    table_names = [table.name for table in answer.tables]
    for variant, headers in answer.variant_headers.items():
        assert len(headers) == EXPECTED_COLUMNS[pack_name], variant
        assert all(name not in cell for name in table_names for cell in headers), (
            f"변형 {variant} 헤더가 정답 테이블 이름을 새는다"
        )


def test_answer_maps_fk_to_parent_pk_column_id(tmp_path: Path) -> None:
    out = _dataset("orders_kr", tmp_path)
    answer = BenchAnswer.model_validate_json((out / "answer.json").read_text(encoding="utf-8"))
    assert answer.answer_version == ANSWER_VERSION == "0.2.0"
    orders = next(table for table in answer.tables if table.name == "orders")
    fk_column = next(column for column in orders.columns if column.is_foreign_key)
    assert fk_column.column_id == "customer_id", "FK의 컬럼 ID는 부모 PK의 컬럼 ID다"
    customers = next(table for table in answer.tables if table.name == "customers")
    assert any(
        column.name == "customer_id" and column.column_id == "customer_id"
        for column in customers.columns
    )
    for table in answer.tables:
        assert table.functional_dependencies
        assert table.row_count == PACKS["orders_kr"].table(table.name).rows


def test_layout_type_annotations() -> None:
    layout = flat_layout(PACKS["orders_kr"])
    assert all(isinstance(column, FlatColumn) for column in layout)
