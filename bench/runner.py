"""벤치 실행기 v0 — 파이프라인 결과를 채점해 점수표를 낸다(T-0022).

결정(리드): 채점 대상은 파이프라인이 만든 3NF 후보(pattern '3nf')다(벤치 정답이
정규화된 원본 스키마이기 때문). 3NF 후보가 없으면 선택된 후보(SelectedSchema)로
대신 채점하고 표에 그 사실을 표시하며, 선택된 후보의 점수도 함께 기록한다.

실행: uv run python -m bench.runner --seed 42 [--pack all]

- 데이터 생성 → 2팩x7변형 analyze --no-llm(프로세스 안에서 호출) → 점수표.
- 실패한 변형은 0점으로 남기고 실패 단계·종료 코드를 기록한다. 원문 오류
  메시지는 남기지 않는다(정책 금지 패턴 1·6). 입력 오류(InputError·
  UnicodeDecodeError)는 S0·종료 1, 그 밖의 내부 오류는 단계 미상·종료 3으로
  분류한다(CLI 예외 체계와 같은 대응).
- 실행기 산출물에는 시각·난수가 없다 — 같은 시드면 같은 표가 나온다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoetl.contracts import SchemaCandidate, SchemaPattern
from autoetl.core.errors import InputError
from autoetl.core.pipeline import run_pipeline
from bench.answer import (
    AnswerForeignKey,
    AnswerFunctionalDependency,
    BenchAnswer,
    BenchPrediction,
    PredictedColumn,
    PredictedTable,
)
from bench.generator import PACKS, build_dataset
from bench.generator.noise import VARIANTS
from bench.scoring import (
    adjusted_rand_index,
    foreign_key_f1,
    functional_dependency_f1,
    functional_dependency_recall,
    primary_key_f1,
    schema_recovery_score,
    type_f1,
)

METRICS = ("type_f1", "pk_f1", "fk_f1", "fd_f1", "ari", "total")
"""점수표 지표 열(카드: 타입·PK·FK·FD·ARI·종합). FD 복원율은 JSON에만 담는다."""

INPUT_FAILURES = (InputError, UnicodeDecodeError)
"""입력 문제로 보는 예외 — 실패 단계 S0, 종료 코드 1(CLI 체계와 같다)."""

_FOREIGN_KEY_REFERENCE = re.compile(r"^([a-z][a-z0-9_]*)\(([a-z][a-z0-9_]*)\)$")
"""TargetColumn.foreign_key의 '다른테이블(컬럼)' 형식."""


@dataclass(frozen=True)
class VariantResult:
    """팩x변형 하나의 실행 결과."""

    pack: str
    variant: str
    candidate_label: str  # 예: '3nf(C2)' · '선택 후보(C1)' · '실패'
    metrics: dict[str, float] | None  # 실패면 None
    selected_total: float | None  # 선택 후보 종합(채점 대상과 같으면 같은 값)
    failure_stage: str | None
    failure_exit_code: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "pack": self.pack,
            "variant": self.variant,
            "candidate": self.candidate_label,
            "metrics": self.metrics,
            "selected_total": self.selected_total,
            "failure": None
            if self.failure_stage is None
            else {"stage": self.failure_stage, "exit_code": self.failure_exit_code},
        }


def to_prediction(candidate: SchemaCandidate) -> BenchPrediction:
    """SchemaCandidate → BenchPrediction 변환(리드 결정 규칙).

    - 컬럼 이름: TargetColumn.source_columns의 원본 컬럼 이름(= 그 변형의 필드
      헤더 문자열). 여러 원본을 합친 컬럼은 첫 원본 이름으로 낸다(v0 미지원).
    - 물리 타입: TargetColumn.physical_type.
    - PK: is_primary_key 컬럼. FK: TargetColumn.foreign_key('테이블(컬럼)' 문자열).
    - FD: 테이블마다 PK → 비키 컬럼(정답 FD 형태와 같다).
    """
    tables: list[PredictedTable] = []
    for table in candidate.tables:
        primary_key = [
            column.source_columns[0] for column in table.columns if column.is_primary_key
        ]
        foreign_keys: list[AnswerForeignKey] = []
        for column in table.columns:
            if column.foreign_key is None:
                continue
            reference = _FOREIGN_KEY_REFERENCE.fullmatch(column.foreign_key)
            if reference is None:
                # 계약 위반 후보 — 코드가 만든 이름이라 안심하고 식별자만 안내한다.
                raise ValueError(
                    f"foreign_key 형식이 '테이블(컬럼)'이 아니다: {column.name}의 참조"
                )
            foreign_keys.append(
                AnswerForeignKey(
                    table=table.name,
                    columns=[column.source_columns[0]],
                    references_table=reference.group(1),
                    references_columns=[reference.group(2)],
                )
            )
        functional_dependencies = [
            AnswerFunctionalDependency(
                table=table.name,
                determinant=primary_key,
                dependent=column.source_columns[0],
            )
            for column in table.columns
            if not column.is_primary_key
        ]
        tables.append(
            PredictedTable(
                columns=[
                    PredictedColumn(
                        name=column.source_columns[0],
                        physical_type=column.physical_type,
                        is_foreign_key=column.foreign_key is not None,
                    )
                    for column in table.columns
                ],
                primary_key=primary_key,
                foreign_keys=foreign_keys,
                functional_dependencies=functional_dependencies,
            )
        )
    return BenchPrediction(tables=tables)


def _score_all(answer: BenchAnswer, prediction: BenchPrediction, variant: str) -> dict[str, float]:
    return {
        "type_f1": type_f1(answer, prediction, variant),
        "pk_f1": primary_key_f1(answer, prediction, variant),
        "fk_f1": foreign_key_f1(answer, prediction, variant),
        "fd_recall": functional_dependency_recall(answer, prediction, variant),
        "fd_f1": functional_dependency_f1(answer, prediction, variant),
        "ari": adjusted_rand_index(answer, prediction, variant),
        "total": schema_recovery_score(answer, prediction, variant),
    }


def _run_variant(
    pack_name: str, variant: str, csv_path: Path, answer: BenchAnswer
) -> VariantResult:
    try:
        result = run_pipeline(csv_path)
    except INPUT_FAILURES:
        # 원문 오류 메시지는 남기지 않는다. 단계·종료 코드만.
        return VariantResult(
            pack=pack_name,
            variant=variant,
            candidate_label="실패",
            metrics=None,
            selected_total=None,
            failure_stage="S0",
            failure_exit_code=1,
        )
    except Exception:
        return VariantResult(
            pack=pack_name,
            variant=variant,
            candidate_label="실패",
            metrics=None,
            selected_total=None,
            failure_stage="unknown",
            failure_exit_code=3,
        )

    third_nf = [
        candidate
        for candidate in result.candidates
        if candidate.pattern is SchemaPattern.THIRD_NORMAL_FORM
    ]
    selected = next(
        (
            candidate
            for candidate in result.candidates
            if candidate.id == result.selected.candidate_id
        ),
        result.candidates[0],
    )
    if third_nf:
        target = third_nf[0]
        label = f"3nf({target.id})"
    else:
        target = selected
        label = f"선택 후보({target.id})"
    metrics = _score_all(answer, to_prediction(target), variant)
    selected_total = (
        metrics["total"]
        if target is selected
        else _score_all(answer, to_prediction(selected), variant)["total"]
    )
    return VariantResult(
        pack=pack_name,
        variant=variant,
        candidate_label=label,
        metrics=metrics,
        selected_total=selected_total,
        failure_stage=None,
        failure_exit_code=None,
    )


def run_bench(
    seed: int, *, pack: str = "all", datasets_dir: Path | None = None
) -> list[VariantResult]:
    """시드 하나로 전 팩(또는 한 팩)x전 변형을 돌려 결과를 돌려준다."""
    pack_names = sorted(PACKS) if pack == "all" else [pack]
    base = datasets_dir if datasets_dir is not None else Path("bench/datasets")
    results: list[VariantResult] = []
    for pack_name in pack_names:
        dataset = PACKS[pack_name]
        out = base / f"{dataset.name}-{seed}"
        build_dataset(dataset, seed=seed, out_dir=out)
        answer = BenchAnswer.model_validate_json((out / "answer.json").read_text(encoding="utf-8"))
        for variant in VARIANTS:
            results.append(_run_variant(pack_name, variant, out / f"{variant}.csv", answer))
    return results


def markdown_table(results: list[VariantResult], *, seed: int) -> str:
    """점수표(Markdown). 팩x변형 행, 지표 열, 채점 대상 후보 표시."""
    lines = [
        f"# 벤치 점수표 (실행기 v0, 시드 {seed})",
        "",
        "| 팩 | 변형 | 타입 F1 | PK F1 | FK F1 | FD F1 | ARI | 종합 | 채점 대상 | 선택 후보 종합 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        if item.metrics is None:
            lines.append(
                f"| {item.pack} | {item.variant} | — | — | — | — | — | 0.00 "
                f"| 실패({item.failure_stage}, 종료 {item.failure_exit_code}) | — |"
            )
            continue
        metrics = item.metrics
        selected_total = (
            "같음" if item.selected_total == metrics["total"] else f"{item.selected_total:.2f}"
        )
        cells = [item.pack, item.variant]
        cells += [f"{metrics[name]:.2f}" for name in METRICS]
        cells += [item.candidate_label, selected_total]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.runner", description="파이프라인 결과 채점")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pack", default="all", choices=["all", *sorted(PACKS)])
    parser.add_argument("--out", type=Path, default=None, help="JSON 결과 파일 경로")
    args = parser.parse_args(argv)

    results = run_bench(args.seed, pack=args.pack)
    print(markdown_table(results, seed=args.seed))
    out = args.out if args.out is not None else Path("bench/results") / f"seed-{args.seed}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"seed": args.seed, "results": [item.to_json() for item in results]}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"JSON: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
