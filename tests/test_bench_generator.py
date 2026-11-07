"""벤치 데이터셋 생성기 v0 검증(T-0015: 접두사 없는 헤더·컬럼 ID·kind 기반 잡음)."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest
from bench.answer import ANSWER_VERSION, BenchAnswer
from bench.generator import PACKS, build_dataset, column_order, flat_layout, flatten, generate_pack
from bench.generator.noise import (
    GROUP_LABELS,
    HARD_GROUP_BLOCK,
    NULL_TOKENS,
    VARIANTS,
    apply_noise,
)
from bench.generator.packs import ColumnSpec, FlatColumn, Pack, TableSpec

from autoetl.contracts.common import PhysicalType

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


def test_hard_variant_uses_nameless_field_row_and_block_labels() -> None:
    pack = PACKS["music"]
    header, rows = flatten(pack, generate_pack(pack, seed=1))
    layout = flat_layout(pack)
    noisy, encoding = apply_noise("hard", header, rows, seed=1, columns=layout)
    assert encoding == "cp949"
    group_row, field_row = noisy[0], noisy[1]
    assert all(cell.startswith("col_") for cell in field_row), "hard 필드 행은 col_01 형식"
    assert field_row == [f"col_{i:02d}" for i in range(1, len(header) + 1)]
    assert len(group_row) == len(header)
    # 그룹 행은 위치 기반 고정 블록 라벨이다(T-0037) — 컬럼 성격·테이블 경계와
    # 무관하므로 라벨만으로 타입·배정을 알 수 없다.
    assert group_row == [f"구분{i // HARD_GROUP_BLOCK + 1}" for i in range(len(header))]
    kinds = [column.kind for column in layout]
    order = column_order("hard", len(header), seed=1)
    shuffled_kinds = [kinds[i] for i in order]
    block_kinds = {label: set() for label in dict.fromkeys(group_row)}
    for i, kind in enumerate(shuffled_kinds):
        block_kinds[group_row[i]].add(kind)
    assert len(block_kinds) >= 2
    assert any(len(block) > 1 for block in block_kinds.values()), (
        "블록 안 컬럼 성격이 섞여 있어야 한다(라벨이 성격을 알려 주지 않는다)"
    )


def test_hard_column_order_is_shuffled_deterministically() -> None:
    pack = PACKS["orders_kr"]
    header, rows = flatten(pack, generate_pack(pack, seed=42))
    layout = flat_layout(pack)
    first, _ = apply_noise("hard", header, rows, seed=42, columns=layout)
    second, _ = apply_noise("hard", header, rows, seed=42, columns=layout)
    assert first == second, "같은 시드는 같은 열 순서를 낸다"
    # 열 단위로 확인: 각 열의 값 집합은 원본 컬럼 값 집합과 같다(순서만 바뀐다).
    assert [row[:] for row in rows] != first[2:], "hard는 컬럼을 섞는다(시드 42)"
    clean, _ = apply_noise("clean", header, rows, seed=42, columns=layout)
    assert [row[:] for row in rows] == clean[1:], "다른 변형은 순서를 유지한다"
    order = column_order("hard", len(header), seed=42)
    assert sorted(order) == list(range(len(header)))
    assert order != list(range(len(header)))
    assert column_order("clean", len(header), seed=42) == list(range(len(header)))


def _noise_forms(kind: str, value: str) -> set[str]:
    """잡음 변형이 만들 수 있는 값 형태(결측 토큰 제외)."""
    if kind == "date":
        parsed = date.fromisoformat(value)
        return {
            value.replace("-", "/"),
            value.replace("-", ""),
            str((parsed - date(1899, 12, 30)).days),
        }
    if kind == "amount":
        return {f"{int(value):,}", f"{int(value):,}원"}
    if kind == "code":
        return {value.lstrip("0") or "0"}
    return set()


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_hard_columns_match_clean_by_column_id(pack_name: str, tmp_path: Path) -> None:
    """hard의 각 컬럼은 같은 컬럼 ID의 clean 컬럼과 같은 데이터다(잡음 셀 제외).

    변형 사이 대조는 위치가 아니라 컬럼 ID로 한다(T-0037) — hard는 열 순서가
    시드 고정 셔플이라 위치가 다르다.
    """
    out = _dataset(pack_name, tmp_path)
    answer = BenchAnswer.model_validate_json((out / "answer.json").read_text(encoding="utf-8"))
    clean_rows = _read_csv(out / "clean.csv")
    hard_rows = _read_csv(out / "hard.csv", encoding="cp949")
    clean_index = {name: i for i, name in enumerate(clean_rows[0])}
    layout = {column.column_id: column for column in flat_layout(PACKS[pack_name])}
    tokens = set(NULL_TOKENS)
    for position, column_id in enumerate(answer.variant_columns["hard"]):
        column = layout[column_id]
        origin = clean_index[column_id]
        for hard_row, clean_row in zip(hard_rows[2:], clean_rows[1:], strict=True):
            value, original = hard_row[position], clean_row[origin]
            if value == original:
                continue
            if column.is_key:
                raise AssertionError(
                    f"키 컬럼 {column_id}은(는) 잡음이 없어 값이 정확히 같아야 한다"
                )
            assert value in tokens or value in _noise_forms(column.kind, original), (
                f"{column_id}: {value!r}은(는) {original!r}의 잡음 형태가 아니다"
            )


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


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_variant_columns_mapping_table(pack_name: str, tmp_path: Path) -> None:
    """변형별 대응표(T-0037): 이름 없는 hard는 컬럼 순서가 정답과 다르다.

    셔플하지 않는 변형은 variant_columns가 정답 순서(= 컬럼 ID 헤더) 그대로다.
    """
    out = _dataset(pack_name, tmp_path)
    answer = BenchAnswer.model_validate_json((out / "answer.json").read_text(encoding="utf-8"))
    ids = [column.column_id for column in flat_layout(PACKS[pack_name])]
    for variant in VARIANTS:
        assert sorted(answer.variant_columns[variant]) == sorted(ids), variant
        assert len(answer.variant_columns[variant]) == len(answer.variant_headers[variant])
    for variant in VARIANTS:
        if variant == "hard":
            assert answer.variant_columns["hard"] != ids, "hard는 컬럼을 섞는다(시드 42)"
        else:
            assert answer.variant_columns[variant] == ids, f"{variant}은 순서를 유지한다"
            # 이름 있는 변형은 헤더 이름 자체가 컬럼 ID다(hard만 col_NN).
            assert answer.variant_headers[variant] == answer.variant_columns[variant]


def test_answer_maps_fk_to_parent_pk_column_id(tmp_path: Path) -> None:
    out = _dataset("orders_kr", tmp_path)
    answer = BenchAnswer.model_validate_json((out / "answer.json").read_text(encoding="utf-8"))
    assert answer.answer_version == ANSWER_VERSION == "0.4.0"
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


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_answer_key_columns_are_integer(pack_name: str, tmp_path: Path) -> None:
    # 정수 대리 키(kind id·fk)의 정답 물리 타입은 integer다(T-0020). 앞자리 0을
    # 보존하는 고정 자릿수 코드(kind code)는 문자열을 유지한다. kind는 평면
    # 배치(flat_layout)가 아니라 팩 테이블 선언에서 온다 — 평면에서 조인 키는
    # 부모 PK 위치에 한 번만 놓이므로 팩에 따라 kind fk가 배치에 안 남는다.
    out = _dataset(pack_name, tmp_path)
    answer = BenchAnswer.model_validate_json((out / "answer.json").read_text(encoding="utf-8"))
    kinds = {
        ((spec.ref or "").split(".")[1] if spec.kind == "fk" else spec.name): spec.kind
        for table in PACKS[pack_name].tables
        for spec in table.columns
    }
    pk_ids = {column for table in answer.tables for column in table.primary_key}
    fk_ids = {
        column for table in answer.tables for fk in table.foreign_keys for column in fk.columns
    }
    assert pk_ids and fk_ids, "두 팩 모두 PK·FK가 있어야 이 테스트가 의미 있다"
    seen_kinds: set[str] = set()
    for table in answer.tables:
        for column in table.columns:
            kind = kinds[column.column_id]
            seen_kinds.add(kind)
            if kind in ("id", "fk"):
                assert column.physical_type is PhysicalType.INTEGER, column.column_id
            if kind == "code":
                assert column.physical_type is PhysicalType.STRING, column.column_id
    assert {"id", "fk"} <= seen_kinds, "키 kind를 실제로 검사했는지 확인"


def test_layout_type_annotations() -> None:
    layout = flat_layout(PACKS["orders_kr"])
    assert all(isinstance(column, FlatColumn) for column in layout)
