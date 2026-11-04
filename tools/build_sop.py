"""SOP 원본(sop/v1.yaml)에서 사람용 문서와 LLM 프롬프트 모음을 생성한다.

- docs/sop-v1.md     사람용 단계별 문서
- docs/prompts-v1.md LLM 프롬프트 모음 (단계 브리프 공통 골격 포함)

생성물은 커밋하고 tests/test_sop.py가 신선도를 검사한다. 문서를 직접 고치지
않고 sop/v1.yaml을 고친 뒤 다시 생성한다.

    uv run python tools/build_sop.py [--out .]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, cast

import yaml

SOP_PATH = Path("sop/v1.yaml")
DOC_NAMES = {"sop": "docs/sop-{version}.md", "prompts": "docs/prompts-{version}.md"}

OWNER_LABELS = {
    "code": "코드",
    "code+llm": "코드+LLM",
    "llm": "LLM",
    "user": "사용자",
}

CARDINALITY_LABELS = {
    "dataset": "데이터셋 1건",
    "per_column": "컬럼별",
    "per_candidate": "후보별",
}


def load_sop(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return cast(dict[str, Any], data)


def _bullet(items: list[str], prefix: str = "-") -> list[str]:
    return [f"{prefix} {item}" for item in items]


def render_sop_doc(sop: dict[str, Any]) -> str:
    version = str(sop["version"])
    lines: list[str] = [
        f"# 표준 분석 절차(SOP) {version}",
        "",
        f"갱신 {sop['updated']} · 원본 `sop/v{version}.yaml`",
        "이 문서는 생성물이다. 직접 고치지 않고 원본을 고친 뒤 `tools/build_sop.py`로 다시 만든다.",
        "",
        "## 요약",
        "",
        "| 단계 | 이름 | 담당 | 출력 계약 | 단위 | 과업 카드 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for step in sop["steps"]:
        llm = cast(dict[str, Any] | None, step.get("llm"))
        cards = ", ".join(cast(list[str], llm["task_cards"])) if llm else "-"
        output = cast(dict[str, Any], step["output"])
        lines.append(
            "| {id} | {name} | {owner} | {contract} | {card} | {cards} |".format(
                id=step["id"],
                name=step["name"],
                owner=OWNER_LABELS.get(str(step["owner"]), str(step["owner"])),
                contract=f"`{output['contract']}`",
                card=CARDINALITY_LABELS.get(str(output["cardinality"]), str(output["cardinality"])),
                cards=cards,
            )
        )
    lines += ["", "## 단계 상세", ""]
    for step in sop["steps"]:
        output = cast(dict[str, Any], step["output"])
        llm = cast(dict[str, Any] | None, step.get("llm"))
        lines += [
            f"### {step['id']} {step['name']}",
            "",
            f"담당: {OWNER_LABELS.get(str(step['owner']), str(step['owner']))}"
            f" · 출력: `{output['contract']}`"
            f" ({CARDINALITY_LABELS.get(str(output['cardinality']), output['cardinality'])})",
            "",
            f"**목표** {step['goal']}",
            "",
            "입력:",
            *_bullet(cast(list[str], step["inputs"])),
            "",
            "점검 항목:",
            *_bullet(cast(list[str], step["checklist"])),
            "",
            "실패 시 행동:",
            *_bullet(cast(list[str], step["on_failure"])),
        ]
        if llm:
            lines += [
                "",
                f"LLM 역할: {llm['role']} (프롬프트는 prompts-{version}.md)",
            ]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_prompts_doc(sop: dict[str, Any]) -> str:
    version = str(sop["version"])
    lines: list[str] = [
        f"# LLM 프롬프트 {version}",
        "",
        f"갱신 {sop['updated']} · 원본 `sop/v{version}.yaml` (생성물 — 직접 고치지 않는다)",
        "",
        "모든 LLM 호출에는 단계 브리프 공통 머리가 붙고 과업별 프롬프트가 이어진다.",
        "`{{ }}` 자리는 하네스가 채운다.",
        "",
        "## 단계 브리프 공통 골격",
        "",
        "```text",
        str(sop["brief_header"]).rstrip(),
        "이번 과업: (과업 프롬프트)",
        "출력 스키마: (계약 JSON Schema)",
        "점검 항목: (단계 점검 항목)",
        "```",
        "",
        "브리프에 들어가는 정보: " + ", ".join(cast(list[str], sop["brief_sections"])),
        "",
    ]
    for step in sop["steps"]:
        llm = cast(dict[str, Any] | None, step.get("llm"))
        if not llm:
            continue
        cards = cast(list[str], llm["task_cards"])
        card_label = ", ".join(cards) if cards else "없음(보조)"
        lines += [
            f"## {step['id']} {step['name']} — {card_label}",
            "",
            f"역할: {llm['role']}",
            "",
            "```text",
            str(llm["prompt"]).rstrip(),
            "```",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def build_docs(sop: dict[str, Any]) -> dict[str, str]:
    version = str(sop["version"])
    return {
        DOC_NAMES["sop"].format(version=version): render_sop_doc(sop),
        DOC_NAMES["prompts"].format(version=version): render_prompts_doc(sop),
    }


def export(out_root: Path) -> list[Path]:
    sop = load_sop(out_root / SOP_PATH)
    written: list[Path] = []
    for rel, content in build_docs(sop).items():
        path = out_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=".", help="저장소 루트(기본: 현재 폴더)")
    args = parser.parse_args(argv)
    paths = export(Path(args.out))
    for path in paths:
        print(f"생성: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
