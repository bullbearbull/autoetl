"""벤치 데이터셋 생성기 v0.

합성 관계형 스키마 팩을 시드에 따라 생성하고, 리프→루트 조인으로 평면화한 뒤,
잡음 변형(clean·결측·형식 혼재·앞자리 0·다중 헤더·CP949·hard)별 CSV로 내보낸다.
정답 스키마(answer.json)와 메니페스트(README.md)를 함께 남긴다.

    uv run python -m bench.generator --out bench/datasets --seed 42

생성물은 저장소에 커밋하지 않는다(.gitignore의 bench/datasets/).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.generator.emit import (
    answer_schema,
    flatten,
    generate_pack,
    write_csv,
    write_json,
)
from bench.generator.noise import VARIANTS, apply_noise
from bench.generator.packs import PACKS, Pack

__all__ = [
    "PACKS",
    "VARIANTS",
    "Pack",
    "answer_schema",
    "build_dataset",
    "flatten",
    "generate_pack",
]


def build_dataset(pack: Pack, seed: int, out_dir: Path) -> dict[str, Any]:
    """팩 하나로 데이터셋 디렉터리를 만든다. 반환: 메니페스트 내용."""
    tables = generate_pack(pack, seed)
    header, rows = flatten(pack, tables)
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, str]] = []
    for variant in VARIANTS:
        out_rows, encoding = apply_noise(variant, header, rows, seed=seed)
        file_name = f"{variant}.csv"
        write_csv(out_dir / file_name, out_rows[0], out_rows[1:], encoding=encoding)
        file_entry: dict[str, Any] = {
            "variant": variant,
            "file": file_name,
            "encoding": encoding,
            "rows": len(rows),
        }
        files.append(file_entry)

    answer = answer_schema(pack, tables)
    write_json(out_dir / "answer.json", answer.model_dump(mode="json"))
    manifest: dict[str, Any] = {
        "generator": "bench.generator v0",
        "pack": pack.name,
        "description": pack.description,
        "license": pack.license,
        "seed": seed,
        "columns": len(header),
        "files": files,
        "answer_version": answer.answer_version,
        "note": "answer.json 형식은 bench/answer.py, 채점은 bench/scoring.py를 따른다.",
    }
    readme = (
        f"# 벤치 데이터셋: {pack.name} (seed {seed})\n\n"
        f"- 설명: {pack.description}\n"
        f"- 라이선스: {pack.license}\n"
        f"- 규모: 행 {len(rows)} x 컬럼 {len(header)}\n\n"
        "| 변형 | 파일 | 인코딩 |\n| --- | --- | --- |\n"
        + "\n".join(
            f"| {item['variant']} | {item['file']} | {item['encoding']} |" for item in files
        )
        + "\n\n정답 스키마는 answer.json. 생성: `uv run python -m bench.generator`.\n"
    )
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    write_json(out_dir / "manifest.json", manifest)
    return manifest
