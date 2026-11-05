"""autoetl CLI v0.

    autoetl analyze <file.csv> --no-llm [--out report.json]

v0은 LLM 없는 경로(--no-llm)만 지원한다. LLM 하네스 연동은 P2 이후.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from autoetl.contracts import Purpose, Report
from autoetl.core.pipeline import run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="autoetl", description="결정론 엔진과 LLM 하네스 기반 로컬 ETL 파이프라인"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="파일을 분석해 보고서를 낸다")
    analyze.add_argument("file", type=Path, help="입력 파일(UTF-8 CSV)")
    analyze.add_argument(
        "--no-llm",
        action="store_true",
        help="LLM 없이 규칙·스텁만 실행 (v0은 이 경로만 지원)",
    )
    analyze.add_argument(
        "--purpose",
        choices=[purpose.value for purpose in Purpose],
        default=Purpose.ANALYTICS.value,
        help="용도(채점 가중치 기준)",
    )
    analyze.add_argument(
        "--out", type=Path, default=None, help="보고서 JSON 파일 경로(기본: stdout)"
    )

    args = parser.parse_args(argv)
    if args.command != "analyze":
        parser.error("알 수 없는 명령")
    if not args.no_llm:
        print("v0은 --no-llm 경로만 지원한다. LLM 하네스는 P2에서 연결된다.", file=sys.stderr)
        return 2
    if not args.file.exists():
        print(f"입력 파일이 없다: {args.file}", file=sys.stderr)
        return 2
    if args.out is not None and args.out.resolve() == args.file.resolve():
        print("--out이 입력 파일과 같은 경로다. 원본을 덮어쓰지 않는다.", file=sys.stderr)
        return 2

    try:
        result = run_pipeline(args.file, purpose=Purpose(args.purpose))
    except UnicodeDecodeError:
        # 예외 원문(값이 포함된 메시지)은 내보내지 않는다(금지 패턴 6).
        print(
            "입력 파일을 UTF-8 텍스트로 읽을 수 없다. "
            "다른 인코딩·형식 지원은 P1(결정론 엔진)에서 추가된다.",
            file=sys.stderr,
        )
        return 1
    except ValidationError:
        print(
            "파이프라인 산출물이 계약을 통과하지 못했다(내부 오류). "
            "입력 파일과 명령을 이슈로 알려달라.",
            file=sys.stderr,
        )
        return 3
    except ValueError as exc:
        # 파이프라인이 직접 던진 안내성 오류(예: 빈 CSV)만 담는다.
        print(f"입력을 처리할 수 없다: {exc}", file=sys.stderr)
        return 1
    report: Report = result.report
    payload = report.model_dump_json(indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
        print(f"보고서: {args.out} ({report.pipeline_version}, {report.sop_version})")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
