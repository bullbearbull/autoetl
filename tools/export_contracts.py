"""계약 JSON Schema 내보내기.

autoetl.contracts.ARTIFACTS의 루트 계약을 schemas/<이름>.schema.json으로 쓴다.
커밋된 schemas/가 최신인지는 tests/test_contracts.py가 검사한다.

    uv run python tools/export_contracts.py [--out schemas]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from autoetl.contracts import ARTIFACTS, CONTRACTS_VERSION


def build_schemas() -> dict[str, dict[str, object]]:
    """이름 → JSON Schema 사전을 만든다."""
    schemas: dict[str, dict[str, object]] = {}
    for name, model in ARTIFACTS.items():
        schema = model.model_json_schema()
        schema["$id"] = f"https://autoetl.dev/schemas/{name}.schema.json"
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schemas[name] = schema
    return schemas


def export(out_dir: Path) -> list[Path]:
    """스키마를 파일로 쓰고 쓴 파일 목록을 반환한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in build_schemas().items():
        path = out_dir / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="schemas", help="출력 폴더(기본: 저장소 루트의 schemas/)")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    paths = export(out_dir)
    print(f"계약 버전 {CONTRACTS_VERSION}: {len(paths)}개 스키마를 {out_dir}/에 썼다")
    for path in paths:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
