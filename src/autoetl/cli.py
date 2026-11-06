"""autoetl CLI v0.

    autoetl analyze <file.csv> --no-llm [--out report.json]

v0은 LLM 없는 경로(--no-llm)만 지원한다. LLM 하네스 연동은 P2 이후.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from autoetl.contracts import Purpose, Report
from autoetl.core.pipeline import InputError, run_pipeline


def _targets_same_file(out: Path, source: Path) -> bool:
    """--out이 입력 파일을 가리키는지 검사한다.

    resolve() 비교는 심볼릭 링크와 같은 경로를 잡지만 하드 링크·대소문자 무시
    파일시스템(macOS 기본 등)의 다른 표기는 못 잡는다. samefile(아이노드 비교)을
    함께 써서 둘 다 막는다. samefile은 양쪽 파일이 있을 때만 쓸 수 있다.
    """
    if out.resolve() == source.resolve():
        return True
    return out.exists() and source.exists() and out.samefile(source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="autoetl", description="결정론 엔진과 LLM 하네스 기반 로컬 ETL 파이프라인"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="파일을 분석해 보고서를 낸다")
    analyze.add_argument("file", type=Path, help="입력 파일(CSV — 인코딩 자동 감지)")
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
    if args.out is not None and _targets_same_file(args.out, args.file):
        print("--out이 입력 파일과 같은 파일이다. 원본을 덮어쓰지 않는다.", file=sys.stderr)
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
    except InputError as exc:
        # 입력 문제만 안내 메시지를 낸다(메시지에 셀 값이 없음이 보장된다).
        print(f"입력을 처리할 수 없다: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # 그 밖의 예외(ValidationError 포함)는 원문을 내보내지 않고 고정 문구만
        # 낸다. 이후 단계가 값을 담은 ValueError를 던져도 새어 나갈 수 없다
        # (금지 패턴 1·6). 실제 데이터 값은 공개 이슈 트래커로 보내지 않는다
        # (정책 1.2절 — 데이터 L2·L3 원문은 저장소 밖으로 나갈 수 없다).
        print(
            "파이프라인이 내부 오류로 끝났다(상세 원문은 표시하지 않는다). "
            "이슈에는 실제 데이터 파일을 첨부하지 말고 실행 명령·autoetl 버전·"
            "컬럼 수·헤더 형태(이름 형태만)를 알려달라.",
            file=sys.stderr,
        )
        return 3
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
