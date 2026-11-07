"""S5 관계 분석 검증(T-0026) — 완료 기준 전항 + 판정 규칙 재현."""

from __future__ import annotations

from pathlib import Path

import pytest
from bench.answer import BenchAnswer
from bench.generator import PACKS, build_dataset
from bench.scoring import _column_ids, _id_map, _truth_fds

from autoetl.contracts import CorrelationMethod, RelationGraph
from autoetl.core.context import AnalysisContext, quote_identifier
from autoetl.core.pipeline import run_pipeline
from autoetl.core.stages import s00_ingest, s01_sampling, s02_profile, s03_types, s05_relations

VARIANTS = ("clean", "missing", "format_mix", "leading_zero", "multi_header", "cp949", "hard")


@pytest.fixture(scope="module")
def datasets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    out_by_pack: dict[str, Path] = {}
    for pack in PACKS.values():
        out = tmp_path_factory.mktemp("s5") / f"{pack.name}-42"
        build_dataset(pack, seed=42, out_dir=out)
        out_by_pack[pack.name] = out
    return out_by_pack


@pytest.fixture(scope="module")
def answers(datasets: dict[str, Path]) -> dict[str, BenchAnswer]:
    return {
        name: BenchAnswer.model_validate_json((out / "answer.json").read_text("utf-8"))
        for name, out in datasets.items()
    }


@pytest.fixture(scope="module")
def graphs(datasets: dict[str, Path]) -> dict[tuple[str, str], RelationGraph]:
    """변형마다 파이프라인을 한 번만 돌려 그래프를 공유한다(S5 무거움)."""
    return {
        (pack_name, variant): run_pipeline(out / f"{variant}.csv").relations
        for pack_name, out in datasets.items()
        for variant in VARIANTS
    }


def _fd_set(graph: RelationGraph, id_map: dict[str, str]) -> set[tuple[frozenset[str], str]]:
    return {
        (
            frozenset(id_map.get(name, name) for name in fd.lhs),
            id_map.get(fd.rhs, fd.rhs),
        )
        for fd in graph.functional_dependencies
    }


# ── 완료 기준: 정답 FD(원본 PK → 비키) 재현율 1.0 ───────────────────────


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_truth_fds_all_found(
    pack_name: str,
    variant: str,
    graphs: dict[tuple[str, str], RelationGraph],
    answers: dict[str, BenchAnswer],
) -> None:
    answer = answers[pack_name]
    truth = _truth_fds(answer)
    mapped = _fd_set(graphs[(pack_name, variant)], _id_map(answer, variant))
    missing = truth - mapped
    assert not missing, f"못 찾은 정답 FD(컬럼 ID): {sorted(missing)}"


# ── 리드 재검토(#174): FD 정밀도(대표 그래프 정규 피복) ──────────────────


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_fd_precision_at_least_half(
    pack_name: str,
    variant: str,
    graphs: dict[tuple[str, str], RelationGraph],
    answers: dict[str, BenchAnswer],
) -> None:
    # 리드 목표: 모든 변형 정밀도 0.5 이상(재현율 1.0과 함께 고정).
    answer = answers[pack_name]
    truth = _truth_fds(answer)
    mapped = _fd_set(graphs[(pack_name, variant)], _id_map(answer, variant))
    hits = len(truth & mapped)
    assert hits == len(truth)
    assert hits / len(mapped) >= 0.5, (
        f"FD 정밀도 {hits / len(mapped):.2f}(정답 {hits} / 예측 {len(mapped)})"
    )


def test_key_lhs_transitive_simplified_on_representative_graph(
    graphs: dict[tuple[str, str], RelationGraph],
) -> None:
    # 리드 #174: 대표 그래프에서 동치 경유 예외가 없어도 FK 직접 종속은 남는다
    # — oi→product_id는 나오고, product_id↔list_price 동치 경유의 oi→list_price는
    # 우변 대표 한정으로 나오지 않는다('대표→멤버'가 product_id→list_price로 말한다).
    graph = graphs[("orders_kr", "clean")]
    oi_rhs = {fd.rhs for fd in graph.functional_dependencies if fd.lhs == ["order_item_id"]}
    assert "product_id" in oi_rhs
    assert "list_price" not in oi_rhs


# ── 완료 기준: 리프 테이블 PK가 후보 키로 나온다 ────────────────────────


@pytest.mark.parametrize("variant", ("clean", "missing", "format_mix"))
@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_leaf_pk_is_candidate_key(
    pack_name: str,
    variant: str,
    graphs: dict[tuple[str, str], RelationGraph],
    answers: dict[str, BenchAnswer],
) -> None:
    answer = answers[pack_name]
    leaf_table = next(table for table in answer.tables if table.name == PACKS[pack_name].leaf)
    pk_id = next(
        column.column_id
        for column in leaf_table.columns
        if column.name == leaf_table.primary_key[0]
    )
    ids = _column_ids(answer)
    header = answer.variant_headers[variant][ids.index(pk_id)]
    graph = graphs[(pack_name, variant)]
    assert any(key.columns == [header] for key in graph.candidate_keys)


# ── 완료 기준: 좌변 크기 설정·결정론 ───────────────────────────────────


def test_default_lhs_size_is_two(graphs: dict[tuple[str, str], RelationGraph]) -> None:
    graph = graphs[("orders_kr", "clean")]
    assert graph.functional_dependencies
    assert all(len(fd.lhs) <= 2 for fd in graph.functional_dependencies)


def test_same_input_yields_same_graph(
    datasets: dict[str, Path], graphs: dict[tuple[str, str], RelationGraph]
) -> None:
    again = run_pipeline(datasets["music"] / "hard.csv").relations
    assert again == graphs[("music", "hard")]


# ── 판정 규칙 재현(합성 데이터) ────────────────────────────────────────


def _run_s5(
    tmp_path: Path, name: str, header: str, rows: list[str], **kwargs: object
) -> RelationGraph:
    """S0~S5만 돌려 그래프를 돌려준다(키워드 인자는 s05_relations.run에 전달)."""
    path = tmp_path / f"{name}.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    spec = s00_ingest.probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = s00_ingest.run(ctx, spec)
        profiles = s02_profile.run(ctx, ingest, s01_sampling.run(ctx))
        types = s03_types.run(ctx, ingest, profiles)
        return s05_relations.run(ctx, ingest, types, profiles, **kwargs)  # type: ignore[arg-type]


def test_missing_column_is_not_key_candidate(tmp_path: Path) -> None:
    # 결측이 있는 컬럼은 키 후보에서 빠진다 — 유일해도 NULL 중복으로 유일성
    # 의미가 없다(리드 결정 2).
    rows = ["1,10", "2,20", "3,30", "4,40"]
    rows += ["5,"] * 6  # id는 유일하지만 결측이 섞였다
    graph = _run_s5(tmp_path, "miss_key", "id,value", rows)
    assert all("id" not in key.columns for key in graph.candidate_keys)


def test_rhs_missing_rows_are_not_violations(tmp_path: Path) -> None:
    # 우변 결측 행은 위반에서 뺀다 — lhs마다 rhs가 하나뿐이면 성립이다.
    rows = ["1,10", "2,20", "3,", "4,40", "5,50", "6,60"]
    graph = _run_s5(tmp_path, "rhs_miss", "id,value", rows)
    assert any(fd.lhs == ["id"] and fd.rhs == "value" for fd in graph.functional_dependencies)


def test_approximate_fd_threshold(tmp_path: Path) -> None:
    # support는 'lhs 값 중 rhs가 유일한 비율'(그룹 기준). 위반 lhs 값이 1%
    # 이하면 채택하고 violation_count(위반 lhs 값의 행 수)를 남긴다. 2%면
    # 기각이다.
    rows = [f"{i},{i}" for i in range(1, 101)] * 2  # a 값마다 2행(그룹 위반 가능)
    rows[0] = "1,999"  # a=1 값이 b 2개 → 위반 lhs 값 1/100(1%)
    graph = _run_s5(tmp_path, "approx1", "a,b", rows)
    fd = next(fd for fd in graph.functional_dependencies if fd.lhs == ["a"] and fd.rhs == "b")
    assert fd.violation_count == 2  # 위반 lhs 값(a=1)의 행 수
    assert fd.support == pytest.approx(0.99)

    rows2 = [f"{i},{i}" for i in range(1, 101)] * 2
    rows2[0] = "1,999"
    rows2[1] = "2,888"  # 위반 lhs 값 2/100(2%)
    graph2 = _run_s5(tmp_path, "approx2", "a,b", rows2)
    assert not any(fd.lhs == ["a"] and fd.rhs == "b" for fd in graph2.functional_dependencies)


def test_minimal_lhs_removes_supersets(tmp_path: Path) -> None:
    # a→b가 성립하면 (a,c)→b는 도출된다 — 최소 좌변만 남긴다.
    rows = [f"{i},{i * 3},{i % 7}" for i in range(1, 51)]
    graph = _run_s5(tmp_path, "minimal", "a,b,c", rows)
    assert any(fd.lhs == ["a"] and fd.rhs == "b" for fd in graph.functional_dependencies)
    assert not any(fd.lhs == ["a", "c"] and fd.rhs == "b" for fd in graph.functional_dependencies)


def test_pk_lhs_transitive_removed_but_fk_kept(
    graphs: dict[tuple[str, str], RelationGraph],
) -> None:
    # 리드 결정 #164: 키 lhs(리프 PK)의 이행은 지운다 — PK→FK→부모 비키
    # (order_date 등)로 도출되는 FD. FK 자체(order_id)·직접 비키(quantity)·
    # 동치 경유 FK(product_id — product_code와 1-1이라 경로 자격 없음)는 남는다.
    graph = graphs[("orders_kr", "clean")]
    oi_rhs = {fd.rhs for fd in graph.functional_dependencies if fd.lhs == ["order_item_id"]}
    assert "order_date" not in oi_rhs  # 이행(oi→order_id→order_date)으로 제거
    assert {"order_id", "quantity", "product_id"} <= oi_rhs  # 직접 종속은 유지


def test_non_key_lhs_transitive_kept(graphs: dict[tuple[str, str], RelationGraph]) -> None:
    # 키가 아닌 lhs의 직접 종속은 유지한다 — order_id→customer_id(FK 직접
    # 종속, 정답 FD)는 e 축소로 지워지지 않는다. 비대표 우변(email)은
    # '대표→멤버'(customer_id→email)로 도달하니 order_id→email은 내지 않는다.
    graph = graphs[("orders_kr", "clean")]
    order_rhs = {fd.rhs for fd in graph.functional_dependencies if fd.lhs == ["order_id"]}
    assert {"order_date", "customer_id"} <= order_rhs
    assert "email" not in order_rhs
    pair_rhs = {
        fd.rhs
        for fd in graph.functional_dependencies
        if fd.lhs == ["customer_id"] and fd.rhs == "email"
    }
    assert pair_rhs == {"email"}  # 대표→멤버가 대신 말한다


def test_unique_single_not_in_compound_lhs(tmp_path: Path) -> None:
    # 유일한 단일 컬럼을 포함한 복합 좌변은 만들지 않는다(단일이 이미 결정한다).
    rows = [f"{i},{i % 5},{i % 3}" for i in range(1, 41)]
    graph = _run_s5(tmp_path, "no_unique_compound", "id,g,h", rows)
    assert any(fd.lhs == ["id"] for fd in graph.functional_dependencies)
    assert all("id" not in fd.lhs or len(fd.lhs) == 1 for fd in graph.functional_dependencies)


def test_equivalent_keys_use_representative(tmp_path: Path) -> None:
    # 서로 1-1인 유일 컬럼은 대표(컬럼 순서 첫 번째)만 결정자다 — 비대표는
    # 대표에 대한 FD만 낸다(유일 컬럼마다 전 컬럼 FD가 반복되지 않는다).
    rows = [f"{i},{i * 100},{i % 7}" for i in range(1, 41)]  # a·dup 유일(1-1)
    graph = _run_s5(tmp_path, "equiv", "a,dup,h", rows)
    lhs_rhs = {(tuple(fd.lhs), fd.rhs) for fd in graph.functional_dependencies}
    assert (("a",), "dup") in lhs_rhs  # 대표는 전부를 결정한다
    assert (("dup",), "a") in lhs_rhs  # 비대표는 대표와의 상호 FD만
    dup_rhs = {rhs for lhs, rhs in lhs_rhs if lhs == ("dup",)}
    assert dup_rhs == {"a"}


def test_fds_carry_full_verification(graphs: dict[tuple[str, str], RelationGraph]) -> None:
    # 채택된 FD는 전수 GROUP BY로 확정했다 — 전부 full이다(실패 닫힘 계약).
    for graph in graphs.values():
        for fd in graph.functional_dependencies:
            assert fd.verification.value == "full"


def test_notes_record_exclusions_and_scope(tmp_path: Path) -> None:
    # 상수·결측 컬럼 제외와 범위 밖 항목이 notes에 남는다(이름·집계만).
    rows = ["1,7,x", "2,7,y", "3,7,", "4,7,w"]  # b 상수, c에 결측 1행
    graph = _run_s5(tmp_path, "consts", "a,b,c", rows)
    assert any("상수 컬럼" in note and "b" in note for note in graph.notes)
    assert any("결측이 있는 컬럼" in note and "c" in note for note in graph.notes)
    assert any("범위 밖" in note for note in graph.notes)


def test_lhs_budget_stops_search(tmp_path: Path) -> None:
    # 예산(검증 좌변 수) 0이면 검증 없이 끝나고 notes에 남은 후보 수가 남는다.
    rows = [f"{i},{i % 5},{i % 3}" for i in range(1, 41)]
    graph = _run_s5(tmp_path, "budget0", "a,b,c", rows, lhs_budget=0)
    assert graph.functional_dependencies == []
    assert any("좌변 후보 예산(0) 초과" in note for note in graph.notes)


def test_numeric_correlations(tmp_path: Path) -> None:
    # 완전 단조 쌍은 Spearman 1.0, 반대 방향은 -1.0이다.
    rows = [f"{i},{i * 2},{100 - i}" for i in range(1, 31)]
    graph = _run_s5(tmp_path, "numeric", "x,y,z", rows)
    values = {(c.a, c.b, c.method): c.value for c in graph.correlations}
    assert values[("x", "y", CorrelationMethod.SPEARMAN)] == pytest.approx(1.0)
    assert values[("x", "y", CorrelationMethod.PEARSON)] == pytest.approx(1.0)
    assert values[("x", "z", CorrelationMethod.SPEARMAN)] == pytest.approx(-1.0)


def test_cramers_v_dependent_beats_independent(tmp_path: Path) -> None:
    # 완전 종속 범주 쌍의 Cramér's V는 1.0, 독립 쌍은 그보다 작다.
    dependent = [f"g{i % 4},h{i % 4}" for i in range(1, 81)]
    graph = _run_s5(tmp_path, "cramers_dep", "g,h", dependent)
    assert any(
        c.method is CorrelationMethod.CRAMERS_V and c.value == pytest.approx(1.0)
        for c in graph.correlations
    )

    # 독립: h는 g와 무관하게 순환(4x4 라틴 방진 — 기대빈도 균등, 카이제곱 0).
    independent = [f"g{i % 4},h{(i * 7 + i // 4) % 4}" for i in range(1, 81)]
    graph2 = _run_s5(tmp_path, "cramers_ind", "g,h", independent)
    assert any(
        c.method is CorrelationMethod.CRAMERS_V and c.value < 0.5 for c in graph2.correlations
    )


# ── 리드 검토(#168) 재현 — 잡동사니 그룹·정규화 실패·동률 순위·키 예산 ────


def test_junk_group_fd_not_adopted_by_row_ratio(tmp_path: Path) -> None:
    # 리드 재현: 고객 5행씩 + '미상'(customer_id=0) 200행에 이메일 30종 —
    # 그룹 기준 support 0.999라도 위반 행 200/5,200(3.8%)이라 채택하지
    # 않는다(채택은 행 기준).
    rows = []
    for i in range(1, 1001):
        for _ in range(5):
            rows.append(f"{i},user{i}@ex.io")
    for i in range(200):
        rows.append(f"0,unknown{i % 30}@ex.io")
    graph = _run_s5(tmp_path, "junk", "customer_id,email", rows)
    assert not any(
        fd.lhs == ["customer_id"] and fd.rhs == "email" for fd in graph.functional_dependencies
    )


def test_normalization_failure_column_not_key(tmp_path: Path) -> None:
    # 리드 재현: 정수 id 600행에 '12a' 1값 — 원문 결측은 아니지만 정규화 뷰에서
    # NULL이므로 결측 컬럼으로 본다. id는 키가 되지 않고 notes에 남는다.
    rows = [str(i) for i in range(1, 601)]
    rows[10] = "12a"
    graph = _run_s5(tmp_path, "norm_fail", "id", rows)
    assert graph.candidate_keys == []
    assert any("결측이 있는 컬럼" in note and "id" in note for note in graph.notes)


def test_spearman_uses_average_ranks(tmp_path: Path) -> None:
    # 리드 재현: x = [1,1,2,3,4,4,5], y = [1..7] — RANK()의 최소 순위(0.9754)가
    # 아니라 동률 평균 순위의 참값 0.9820이 나온다.
    rows = ["1,1", "1,2", "2,3", "3,4", "4,5", "4,6", "5,7"]
    graph = _run_s5(tmp_path, "spear_tie", "x,y", rows)
    spearman = next(c.value for c in graph.correlations if c.method is CorrelationMethod.SPEARMAN)
    assert spearman == pytest.approx(0.9820, abs=5e-5)  # 리드 참값 표기(4자리)


def test_key_budget_stops_search(tmp_path: Path) -> None:
    # 키 탐색도 작업량 예산(검증 조합 수)을 센다 — 초과하면 완료 크기와 남은
    # 조합 수를 notes에 남긴다.
    rows = [f"{i % 7},{i % 5},{i % 3},{i % 11}" for i in range(1, 201)]
    graph = _run_s5(tmp_path, "key_budget", "a,b,c,d", rows, key_budget=1)
    assert any("키 조합 예산(1) 초과" in note for note in graph.notes)


# ── 리드 재검토(3차) — 동치 클래스 3개 이상·근사 경유·피복 동치 ────────


def test_equivalence_class_members_reach_representative_only(tmp_path: Path) -> None:
    # 리드 재현: 고객 60명이 주문 10행씩(600행)인 데이터에서 customer_id·email·
    # 서로 1-1이라 동치 클래스가 4개짜리가 된다. 클래스 안 출력은 대표↔멤버만 —
    # 멤버→멤버(email→phone)를 내면 축소 e가 그것을 경유로 멤버→대표
    # (email→customer_id)를 지워 대체 키 정보가 사라진다.
    rows = [
        f"{i},{(i - 1) % 60 + 1},user{(i - 1) % 60 + 1}@ex.io,"
        f"010-{(i - 1) % 60 + 1:04d},g{(i - 1) % 60 + 1:02d}"
        for i in range(1, 601)
    ]
    graph = _run_s5(tmp_path, "equiv4", "order_id,customer_id,email,phone,grade", rows)
    fd_pairs = {(tuple(fd.lhs), fd.rhs) for fd in graph.functional_dependencies}
    assert (("email",), "customer_id") in fd_pairs  # 멤버→대표가 남는다
    assert (("phone",), "customer_id") in fd_pairs
    assert (("customer_id",), "email") in fd_pairs  # 대표→멤버도 남는다
    assert (("email",), "phone") not in fd_pairs  # 멤버→멤버는 내지 않는다
    assert (("phone",), "grade") not in fd_pairs


@pytest.mark.parametrize("pack_name", sorted(PACKS))
def test_output_closure_covers_exact_single_fds(
    datasets: dict[str, Path],
    graphs: dict[tuple[str, str], RelationGraph],
    pack_name: str,
) -> None:
    # 리드 재검토(3차): 정규화 뷰의 정확 단일 FD 전체(E)가 출력 정확 FD의 이행
    # 폐포로 전부 도출되는지 확인한다 — '동시 삭제'류 결함을 한 규칙에 묶이지
    # 않고 막는다.
    path = datasets[pack_name] / "clean.csv"
    spec = s00_ingest.probe_source(path)
    with AnalysisContext(path, spec=spec) as ctx:
        ingest = s00_ingest.run(ctx, spec)
        profiles = s02_profile.run(ctx, ingest, s01_sampling.run(ctx))
        s03_types.run(ctx, ingest, profiles)
        view = ctx.normalized_view
        assert view is not None
        quoted = {
            column.name: quote_identifier(ctx.column_id_of(column.index))
            for column in ingest.columns
        }
        names = [column.name for column in ingest.columns]
        cardinality: dict[str, int] = {}
        for name in names:
            count_row = ctx.connection.execute(
                f"SELECT COUNT(DISTINCT {quoted[name]}) FROM {view}"
            ).fetchone()
            cardinality[name] = int(count_row[0] or 0) if count_row is not None else 0
        exact: set[tuple[str, str]] = set()
        for lhs in names:
            for rhs in names:
                # 상수 우변은 자명하다(S5가 좌변·우변 후보에서 뺀다) — E에서도 뺀다.
                if lhs == rhs or cardinality[rhs] <= 1:
                    continue
                row = ctx.connection.execute(
                    f"SELECT EXISTS (SELECT 1 FROM {view} WHERE {quoted[lhs]} IS NOT NULL "
                    f"AND {quoted[rhs]} IS NOT NULL GROUP BY {quoted[lhs]} "
                    f"HAVING COUNT(DISTINCT {quoted[rhs]}) > 1)"
                ).fetchone()
                if row is not None and not row[0]:
                    exact.add((lhs, rhs))
    edges = {
        (fd.lhs[0], fd.rhs)
        for fd in graphs[(pack_name, "clean")].functional_dependencies
        if len(fd.lhs) == 1 and fd.violation_count == 0
    }
    derived: dict[str, set[str]] = {}

    def closure(start: str) -> set[str]:
        if start not in derived:
            reachable: set[str] = set()
            frontier = [start]
            while frontier:
                node = frontier.pop()
                for a, b in edges:
                    if a == node and b not in reachable:
                        reachable.add(b)
                        frontier.append(b)
            derived[start] = reachable
        return derived[start]

    missing = {(lhs, rhs) for lhs, rhs in exact if rhs not in closure(lhs)}
    assert not missing, f"출력 폐포로 도출되지 않는 정확 단일 FD: {sorted(missing)}"


def test_compound_lhs_via_album_fd_reduced(
    graphs: dict[tuple[str, str], RelationGraph],
) -> None:
    # 리드 재검토(3차): 경유 제거를 근사로 일반화 — X→t의 위반 행 수가 X→c
    # 이하이고 t→c가 정확이면 X→c는 지운다. music clean의 (genre, release_date)
    # 근사 FD(위반 8행)는 → album_id 하나로 줄어난다(이전 5개).
    graph = graphs[("music", "clean")]
    count = sum(
        1 for fd in graph.functional_dependencies if set(fd.lhs) == {"genre", "release_date"}
    )
    assert count <= 1, f"(genre, release_date) 좌변 FD가 {count}개다"
