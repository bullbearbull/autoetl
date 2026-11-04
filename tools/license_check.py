"""런타임 의존성 라이선스 자동 검사.

프로젝트(autoetl)의 런타임 의존성과 전이 의존성을 설치 환경 메타데이터에서 읽어
각 패키지의 라이선스가 허용 목록에 들어 있는지 검사한다. 표준 라이브러리만 쓴다.

    uv run python tools/license_check.py

허용 목록은 docs/PLAN.md의 의존성 정책(핵심 패키지는 MIT·BSD·Apache-2.0·PSF·
MPL-2.0 같은 허용형만)을 따른다. 목록을 늘리는 변경은 소유자 검토 대상이다
(AGENTS.md 3절). 판정 규칙:

1. SPDX 라이선스 식(License-Expression)을 우선한다. AND는 양쪽 모두, OR는
   한쪽이라도 허용이면 통과다. WITH 예외 조항은 허용 목록에 있어야 통과한다.
2. 식이 없으면 License 분류자, 그다음 License 필드를 본다.
3. 판별할 수 없거나 목록에 없으면 실패한다.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable, Iterator
from importlib import metadata

PROJECT_NAME = "autoetl"

ALLOWED_LICENSES = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "0BSD",
        "PSF-2.0",
        "Python-2.0",
        "MPL-2.0",
        "ISC",
        "Unlicense",
        "Zlib",
        "BSL-1.0",
        "CC0-1.0",
    }
)

# SPDX 식별자에 붙을 수 있는 예외 조항 중 통과를 허용하는 것.
ALLOWED_EXCEPTIONS = frozenset({"LLVM-exception"})

# "License :: OSI Approved :: ..." 분류자 → SPDX 식별자.
# BSD License 분류자는 2·3-Clause를 구분하지 못하는데 둘 다 허용 목록에
# 들어 있으므로 대표값으로 BSD-3-Clause를 쓴다.
CLASSIFIER_TO_SPDX: dict[str, str] = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: Apache License 2.0": "Apache-2.0",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: 3-Clause BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: New BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: 2-Clause BSD License": "BSD-2-Clause",
    "License :: OSI Approved :: Simplified BSD License": "BSD-2-Clause",
    "License :: OSI Approved :: FreeBSD License": "BSD-2-Clause",
    "License :: OSI Approved :: 0-Clause BSD License": "0BSD",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: ISC License": "ISC",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
    "License :: OSI Approved :: zlib/libpng License": "Zlib",
    "License :: OSI Approved :: Boost Software License 1.0 (BSL-1.0)": "BSL-1.0",
    "License :: OSI Approved :: CC0 1.0 Universal": "CC0-1.0",
}

# 옛 형식 License 필드 값(소문자) → SPDX 식별자. 정확히 일치할 때만 인정한다.
LICENSE_FIELD_ALIASES: dict[str, str] = {
    "mit": "MIT",
    "mit license": "MIT",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "bsd": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "simplified bsd license": "BSD-2-Clause",
    "0bsd": "0BSD",
    "python software foundation license": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "isc": "ISC",
    "isc license": "ISC",
    "mpl-2.0": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "mozilla public license 2.0": "MPL-2.0",
    "the unlicense": "Unlicense",
    "unlicense": "Unlicense",
    "zlib": "Zlib",
    "zlib license": "Zlib",
    "bsl-1.0": "BSL-1.0",
    "boost software license 1.0": "BSL-1.0",
    "cc0-1.0": "CC0-1.0",
    "cc0 1.0 universal": "CC0-1.0",
}

_ALLOWED_BY_UPPER = {lid.upper(): lid for lid in ALLOWED_LICENSES}
_ALLOWED_EXCEPTIONS_UPPER = {exc.upper() for exc in ALLOWED_EXCEPTIONS}

_TOKEN_RE = re.compile(r"\(|\)|AND|OR|WITH|[^\s()]+", re.IGNORECASE)
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


def evaluate_license_expression(expr: str) -> bool:
    """SPDX 라이선스 식이 허용 목록 안에 들어 있는지 판정한다."""
    tokens: list[str] = _TOKEN_RE.findall(expr)
    if not tokens:
        raise ValueError("빈 라이선스 식")
    pos = 0

    def peek() -> str | None:
        return tokens[pos].upper() if pos < len(tokens) else None

    def take(expected: str | None = None) -> str:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError(f"식이 중간에 끝남: {expr!r}")
        token = tokens[pos]
        pos += 1
        if expected is not None and token.upper() != expected:
            raise ValueError(f"{expected!r}이(가) 와야 할 자리에 {token!r}: {expr!r}")
        return token

    def parse_or() -> bool:
        value = parse_and()
        while peek() == "OR":
            take()
            value = parse_and() or value
        return value

    def parse_and() -> bool:
        value = parse_with()
        while peek() == "AND":
            take()
            value = parse_with() and value
        return value

    def parse_with() -> bool:
        value = parse_primary()
        while peek() == "WITH":
            take()
            exception = take()
            if exception.upper() not in _ALLOWED_EXCEPTIONS_UPPER:
                value = False
        return value

    def parse_primary() -> bool:
        token = take()
        if token == "(":
            value = parse_or()
            take(")")
            return value
        if token == ")":
            raise ValueError(f"짝이 맞지 않는 닫는 괄호: {expr!r}")
        return token.upper() in _ALLOWED_BY_UPPER

    result = parse_or()
    if pos != len(tokens):
        raise ValueError(f"남은 토큰 {tokens[pos:]}: {expr!r}")
    return result


def _header(md: metadata.PackageMetadata, name: str) -> str:
    """메타데이터 헤더 하나를 읽는다. 여러 값이 있으면 마지막 값을 쓴다."""
    values = md.get_all(name)
    return values[-1].strip() if values else ""


def resolve_license(md: metadata.PackageMetadata) -> tuple[str, bool, str]:
    """메타데이터 하나에서 라이선스를 판정한다. 반환은 (표기용 값, 허용 여부, 근거)."""
    expr = _header(md, "License-Expression")
    if expr:
        try:
            return expr, evaluate_license_expression(expr), "License-Expression"
        except ValueError as exc:
            return expr, False, f"SPDX 식 파싱 실패({exc})"

    spdx_ids = [
        spdx
        for classifier in md.get_all("Classifier") or []
        if (spdx := CLASSIFIER_TO_SPDX.get(classifier.strip()))
    ]
    if spdx_ids:
        unique = list(dict.fromkeys(spdx_ids))
        return ", ".join(unique), all(s in ALLOWED_LICENSES for s in unique), "Classifier"

    field = _header(md, "License")
    if field:
        spdx = LICENSE_FIELD_ALIASES.get(field.lower())
        if spdx is not None:
            return spdx, spdx in ALLOWED_LICENSES, "License 필드"
        return field, False, "License 필드(인식 불가)"

    return "", False, "라이선스 정보 없음"


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str | None:
    head = requirement.split(";", 1)[0]
    match = _NAME_RE.match(head)
    return match.group(1) if match else None


def _resolve_installed(name: str) -> metadata.Distribution | None:
    try:
        return metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None


def iter_runtime_dependencies(
    root: str = PROJECT_NAME,
    *,
    resolve: Callable[[str], metadata.Distribution | None] | None = None,
) -> Iterator[metadata.Distribution]:
    """root부터 런타임 의존성(전이 포함)을 너비 우선으로 돌며 배포 객체를 낸다.

    extra 조건부 의존성도 이름만으로 큐에 들어가지만, 설치 환경에 없으면
    건너뛴다. 검사는 실제 설치된 패키지만 대상으로 한다.
    """
    if resolve is None:
        resolve = _resolve_installed
    queue = [root]
    seen = {_canonical(root)}
    while queue:
        dist = resolve(queue.pop(0))
        if dist is None:
            continue
        yield dist
        for requirement in dist.requires or []:
            dep = _requirement_name(requirement)
            if dep and (key := _canonical(dep)) not in seen:
                seen.add(key)
                queue.append(dep)


def main(deps: Iterable[metadata.Distribution] | None = None) -> int:
    """모든 런타임 의존성의 라이선스를 검사하고 결과를 출력한다. 0이 통과다."""
    if deps is None:
        deps = iter_runtime_dependencies()
    failures = 0
    for dist in deps:
        name = _header(dist.metadata, "Name")
        if _canonical(name) == _canonical(PROJECT_NAME):
            continue  # 프로젝트 자체의 라이선스는 별도 결정 사항이다
        display, allowed, basis = resolve_license(dist.metadata)
        mark = "허용" if allowed else "거부"
        print(f"{mark}  {name} {dist.version}  {display or '(라이선스 없음)'}  [{basis}]")
        failures += not allowed
    if failures:
        print(f"허용 목록에 없는 라이선스 {failures}건 — 의존성 정책 위반", file=sys.stderr)
        return 1
    print("모든 런타임 의존성이 허용형 라이선스 안에 있다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
