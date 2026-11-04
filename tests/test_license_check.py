"""tools/license_check.py 단위 테스트."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "license_check", Path(__file__).resolve().parents[1] / "tools" / "license_check.py"
)
assert _SPEC is not None and _SPEC.loader is not None
license_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(license_check)


class FakeDist(metadata.Distribution):
    """METADATA 문자열로 만드는 가짜 배포 객체."""

    def __init__(self, metadata_text: str) -> None:
        self._metadata_text = metadata_text

    def read_text(self, filename: str) -> str | None:
        return self._metadata_text if filename == "METADATA" else None

    def locate_file(self, path: object) -> Path:
        raise NotImplementedError


def make_dist(
    name: str,
    *,
    requires: list[str] | None = None,
    headers: list[str] | None = None,
) -> FakeDist:
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        "Version: 1.2.3",
        *(f"Requires-Dist: {requirement}" for requirement in requires or []),
        *(headers or []),
    ]
    return FakeDist("\n".join(lines) + "\n")


class TestEvaluateExpression:
    def test_single_allowed(self) -> None:
        assert license_check.evaluate_license_expression("MIT")
        assert license_check.evaluate_license_expression("Apache-2.0")

    def test_single_denied(self) -> None:
        assert not license_check.evaluate_license_expression("GPL-3.0-only")

    def test_or(self) -> None:
        assert license_check.evaluate_license_expression("MIT OR Apache-2.0")
        assert license_check.evaluate_license_expression("MIT OR GPL-3.0-only")
        assert not license_check.evaluate_license_expression("GPL-2.0-only OR GPL-3.0-only")

    def test_and(self) -> None:
        assert license_check.evaluate_license_expression("MIT AND Apache-2.0")
        assert not license_check.evaluate_license_expression("MIT AND GPL-3.0-only")

    def test_parens(self) -> None:
        assert license_check.evaluate_license_expression("(MIT OR GPL-3.0-only) AND BSD-3-Clause")
        # OR의 한 가지가 허용형이면 그 가지를 선택해 만족할 수 있다.
        assert license_check.evaluate_license_expression("MIT AND (GPL-3.0-only OR ISC)")
        both_gpl = license_check.evaluate_license_expression(
            "MIT AND (GPL-3.0-only OR GPL-2.0-only)"
        )
        assert not both_gpl

    def test_with_exception(self) -> None:
        assert license_check.evaluate_license_expression("Apache-2.0 WITH LLVM-exception")
        assert not license_check.evaluate_license_expression("Apache-2.0 WITH GPL-exception")

    def test_case_insensitive_ids(self) -> None:
        assert license_check.evaluate_license_expression("apache-2.0 OR mit")

    @pytest.mark.parametrize("bad", ["", "MIT AND", "MIT OR", "(MIT", "MIT)"])
    def test_malformed_raises(self, bad: str) -> None:
        with pytest.raises(ValueError):
            license_check.evaluate_license_expression(bad)


class TestResolveLicense:
    def test_expression_header(self) -> None:
        dist = make_dist("foo", headers=["License-Expression: MIT OR Apache-2.0"])
        display, allowed, basis = license_check.resolve_license(dist.metadata)
        assert allowed
        assert display == "MIT OR Apache-2.0"
        assert basis == "License-Expression"

    def test_classifier(self) -> None:
        dist = make_dist(
            "bar",
            headers=[
                "Classifier: License :: OSI Approved :: MIT License",
                "Classifier: Programming Language :: Python :: 3",
            ],
        )
        display, allowed, basis = license_check.resolve_license(dist.metadata)
        assert allowed
        assert display == "MIT"
        assert basis == "Classifier"

    def test_denied_classifier(self) -> None:
        gpl = "Classifier: License :: OSI Approved :: GNU General Public License v3 (GPLv3)"
        dist = make_dist("baz", headers=[gpl])
        _display, allowed, _basis = license_check.resolve_license(dist.metadata)
        assert not allowed

    def test_license_field_alias(self) -> None:
        dist = make_dist("qux", headers=["License: Apache License, Version 2.0"])
        _display, allowed, basis = license_check.resolve_license(dist.metadata)
        assert allowed
        assert basis == "License 필드"

    def test_unknown_license_field_fails_closed(self) -> None:
        dist = make_dist("quux", headers=["License: see LICENSE file for details"])
        _display, allowed, _basis = license_check.resolve_license(dist.metadata)
        assert not allowed

    def test_missing_license_fails_closed(self) -> None:
        dist = make_dist("corge")
        _display, allowed, basis = license_check.resolve_license(dist.metadata)
        assert not allowed
        assert basis == "라이선스 정보 없음"


def _walk(table: dict[str, FakeDist], root: str) -> Iterator[metadata.Distribution]:
    def resolve(name: str) -> metadata.Distribution | None:
        return table.get(name.lower())

    yield from license_check.iter_runtime_dependencies(root, resolve=resolve)


class TestIterDependencies:
    def test_walks_transitive_and_cycles(self) -> None:
        table: dict[str, FakeDist] = {
            "a": make_dist("a", requires=['b>=1 ; python_version < "3.11"', "a"]),
            "b": make_dist("b", requires=["A"]),
        }
        names = [d.metadata["Name"] for d in _walk(table, "a")]
        assert names == ["a", "b"]

    def test_skips_missing(self) -> None:
        table = {"a": make_dist("a", requires=["ghost"])}
        names = [d.metadata["Name"] for d in _walk(table, "a")]
        assert names == ["a"]


class TestMain:
    def test_pass(self, capsys: pytest.CaptureFixture[str]) -> None:
        project = make_dist("autoetl", requires=["good"])
        good = make_dist("good", headers=["License-Expression: BSD-3-Clause"])
        assert license_check.main([project, good]) == 0
        assert "허용" in capsys.readouterr().out

    def test_fail_on_unknown(self, capsys: pytest.CaptureFixture[str]) -> None:
        project = make_dist("autoetl", requires=["mystery"])
        mystery = make_dist("mystery")
        assert license_check.main([project, mystery]) == 1

    def test_no_dependencies(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert license_check.main([make_dist("autoetl")]) == 0
        assert "모든 런타임 의존성" in capsys.readouterr().out
