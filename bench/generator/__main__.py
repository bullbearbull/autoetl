"""CLI 진입점: python -m bench.generator"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bench.generator import build_dataset
from bench.generator.packs import PACKS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.generator",
        description="벤치 데이터셋 생성기 v0 — 합성 관계형 팩을 평면 CSV·잡음 변형으로 내보낸다",
    )
    parser.add_argument("--out", default="bench/datasets", help="출력 폴더(기본: bench/datasets)")
    parser.add_argument("--seed", type=int, default=42, help="생성 시드(기본: 42)")
    parser.add_argument("--pack", default="all", help=f"팩 이름({', '.join(PACKS)}) 또는 all(기본)")
    args = parser.parse_args(argv)

    names = list(PACKS) if args.pack == "all" else [args.pack]
    for name in names:
        if name not in PACKS:
            print(f"알 수 없는 팩: {name}", file=sys.stderr)
            return 2
    out_root = Path(args.out)
    for name in names:
        pack = PACKS[name]
        out_dir = out_root / f"{name}-{args.seed}"
        manifest = build_dataset(pack, args.seed, out_dir)
        print(f"{name}: 파일 {len(manifest['files'])}개 → {out_dir} (라이선스: {pack.license})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
