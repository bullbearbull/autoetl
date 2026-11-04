"""SOP v1 원본과 생성물 검증."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast

from autoetl.contracts import ARTIFACTS

_SPEC = importlib.util.spec_from_file_location(
    "build_sop", Path(__file__).resolve().parents[1] / "tools" / "build_sop.py"
)
assert _SPEC is not None and _SPEC.loader is not None
build_sop = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_sop)

ROOT = Path(__file__).resolve().parents[1]
VALID_OWNERS = {"code", "code+llm", "llm", "user"}


def _sop() -> dict[str, Any]:
    return cast(dict[str, Any], build_sop.load_sop(ROOT / "sop" / "v1.yaml"))


def _steps(sop: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], sop["steps"])


def test_steps_cover_s0_to_s14_in_order() -> None:
    ids = [str(step["id"]) for step in _steps(_sop())]
    assert ids == [f"S{i}" for i in range(15)]


def test_outputs_cover_all_contracts_exactly() -> None:
    outputs = {str(cast(dict[str, Any], step["output"])["contract"]) for step in _steps(_sop())}
    assert outputs == set(ARTIFACTS)


def test_step_fields_complete() -> None:
    for step in _steps(_sop()):
        assert str(step["owner"]) in VALID_OWNERS, step["id"]
        assert step["goal"]
        assert cast(list[str], step["inputs"])
        assert cast(list[str], step["checklist"]), step["id"]
        assert cast(list[str], step["on_failure"]), step["id"]


def test_llm_steps_have_prompt_and_cards() -> None:
    llm_steps = [step for step in _steps(_sop()) if step.get("llm")]
    assert llm_steps, "LLM 단계가 최소 하나는 있어야 한다"
    for step in llm_steps:
        llm = cast(dict[str, Any], step["llm"])
        assert llm["role"]
        assert "task_cards" in llm
        assert "과업:" in str(llm["prompt"]), step["id"]
        assert "출력 스키마" in str(llm["prompt"]), step["id"]
    owners_with_llm = {str(step["owner"]) for step in llm_steps}
    assert owners_with_llm <= {"code+llm", "llm"}


def test_inputs_reference_known_contracts() -> None:
    contract_keys = set(ARTIFACTS)
    for step in _steps(_sop()):
        for item in cast(list[str], step["inputs"]):
            if item.isidentifier() or item in contract_keys:
                assert item in contract_keys, (step["id"], item)


def test_generated_docs_are_fresh() -> None:
    sop = _sop()
    for rel, content in build_sop.build_docs(sop).items():
        on_disk = (ROOT / rel).read_text(encoding="utf-8")
        assert on_disk == content, rel


def test_prompts_doc_lists_all_llm_steps() -> None:
    sop = _sop()
    prompts_md = build_sop.build_docs(sop)["docs/prompts-v1.md"]
    for step in _steps(_sop()):
        if step.get("llm"):
            assert f"## {step['id']} " in prompts_md
