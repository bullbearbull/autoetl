"""벤치 데이터셋 생성기 v0 검증."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from bench.generator import PACKS, build_dataset, flatten, generate_pack
from bench.generator.noise import VARIANTS, apply_noise


def _read_csv(path: Path, encoding: str = "utf-8") -> list[list[str]]:
    with path.open(encoding=encoding, newline="") as handle:
        return list(csv.reader(handle))


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_generation_is_deterministic(pack_name: str) -> None:
    pack = PACKS[pack_name]
    first = generate_pack(pack, seed=42)
    second = generate_pack(pack, seed=42)
    assert first == second
    other = generate_pack(pack, seed=43)
    assert first != other


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


def test_flatten_keeps_leaf_row_count_and_unique_ids() -> None:
    for pack in PACKS.values():
        tables = generate_pack(pack, seed=7)
        header, rows = flatten(pack, tables)
        assert len(rows) == len(tables[pack.leaf])
        id_column = header.index(f"{pack.leaf}_{pack.table(pack.leaf).pk[0]}")
        ids = [row[id_column] for row in rows]
        assert len(ids) == len(set(ids)), "리프 PK는 평면 테이블에서도 유일해야 한다"
        # 평면 테이블은 팩의 모든 테이블 컬럼을 담는다(차원 테이블 포함).
        assert len(header) == sum(len(table.columns) for table in pack.tables)


def test_missing_variant_injects_null_tokens() -> None:
    pack = PACKS["orders_kr"]
    _header, rows = flatten(pack, generate_pack(pack, seed=7))
    noisy, _encoding = apply_noise("missing", _header, rows, seed=7, missing_rate=0.3)
    body = noisy[1:]
    flat_values = {value for row in body for value in row}
    assert flat_values & {"-", "N/A", "해당없음"}, "결측 토큰이 하나라도 들어가야 한다"


def test_format_mix_produces_date_and_amount_variants() -> None:
    pack = PACKS["orders_kr"]
    header, rows = flatten(pack, generate_pack(pack, seed=7))
    noisy, _encoding = apply_noise("format_mix", header, rows, seed=7)
    date_index = header.index("orders_order_date")
    amount_index = header.index("products_list_price")
    date_values = {row[date_index] for row in noisy[1:]}
    amount_values = {row[amount_index] for row in noisy[1:]}
    assert any("-" not in value for value in date_values), "yyyy mmdd·시리얼 변형 필요"
    assert any(("," in value or "원" in value) for value in amount_values)


def test_leading_zero_variant_strips_some_zeros() -> None:
    pack = PACKS["orders_kr"]
    header, rows = flatten(pack, generate_pack(pack, seed=7))
    code_index = header.index("products_product_code")
    clean_codes = [row[code_index] for row in rows]
    noisy, _encoding = apply_noise("leading_zero", header, rows, seed=7)
    noisy_codes = [row[code_index] for row in noisy[1:]]
    stripped = sum(1 for value in noisy_codes if value and not value.startswith("0")) - sum(
        1 for value in clean_codes if value and not value.startswith("0")
    )
    assert stripped > 0, "앞자리 0이 떨어져 나간 행이 있어야 한다"


def test_multi_header_and_cp949_variants(tmp_path: Path) -> None:
    pack = PACKS["orders_kr"]
    header, rows = flatten(pack, generate_pack(pack, seed=7))
    multi, _encoding = apply_noise("multi_header", header, rows, seed=7)
    assert len(multi[0]) == len(header)
    assert multi[1] == header

    cp949_rows, encoding = apply_noise("cp949", header, rows, seed=7)
    path = tmp_path / "cp949.csv"
    from bench.generator.emit import write_csv

    write_csv(path, cp949_rows[0], cp949_rows[1:], encoding=encoding)
    assert encoding == "cp949"
    content = path.read_text(encoding="cp949")
    assert "결제완료" in content or "고객" in content


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_build_dataset_writes_complete_artifacts(pack_name: str, tmp_path: Path) -> None:
    pack = PACKS[pack_name]
    out_dir = tmp_path / f"{pack.name}-42"
    manifest = build_dataset(pack, seed=42, out_dir=out_dir)

    assert (out_dir / "README.md").exists()
    assert (out_dir / "answer.json").exists()
    assert (out_dir / "manifest.json").exists()
    assert [item["variant"] for item in manifest["files"]] == list(VARIANTS)
    assert manifest["license"] == pack.license

    clean = _read_csv(out_dir / "clean.csv")
    assert clean[0] and len(clean) - 1 == pack.table(pack.leaf).rows
    hard = _read_csv(out_dir / "hard.csv", encoding="cp949")
    assert len(hard) - 2 == len(clean) - 1, "hard는 헤더가 2행(다중 헤더)이다"

    answer = json.loads((out_dir / "answer.json").read_text(encoding="utf-8"))
    answer_tables = {table["name"] for table in answer["tables"]}
    assert answer_tables == {table.name for table in pack.tables}
    fk_graph = {
        (table["name"], fk["references"].split(".")[0])
        for table in answer["tables"]
        for fk in table["foreign_keys"]
    }
    declared = {
        (table.name, (column.ref or "").split(".")[0])
        for table in pack.tables
        for column in table.columns
        if column.kind == "fk"
    }
    assert fk_graph == declared


def test_hard_variant_is_cp949_multi_header() -> None:
    pack = PACKS["music"]
    header, rows = flatten(pack, generate_pack(pack, seed=1))
    noisy, encoding = apply_noise("hard", header, rows, seed=1)
    assert encoding == "cp949"
    assert len(noisy[1]) == len(header), "두 번째 행이 필드 이름이어야 한다"
