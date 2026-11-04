"""Command line interface.

    python tools/board/board.py <command> [options]

Write commands need to know who is acting: pass --as NAME or set BOARD_AGENT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .core import (
    PRIORITIES,
    PRIORITY_LABELS,
    STATUS_LABELS,
    STATUSES,
    USER_POST_KINDS,
    Board,
    BoardError,
    check_name,
    find_default_db,
)

KIND_LABELS = {
    "progress": "진행",
    "decision": "결정",
    "question": "질문",
    "blocker": "막힘",
    "handoff": "인계",
    "note": "메모",
    "status": "상태",
    "review_request": "검토 요청",
    "review_result": "검토 결과",
}


# --------------------------------------------------------------------------- utilities


def _db_path(args: argparse.Namespace) -> Path:
    return Path(args.db).expanduser().resolve() if args.db else find_default_db()


def _open(args: argparse.Namespace) -> Board:
    return Board(_db_path(args))


def _actor(args: argparse.Namespace) -> str:
    name = args.actor or os.environ.get("BOARD_AGENT")
    if not name:
        raise BoardError("누가 하는 작업인지 알 수 없습니다. --as 이름 또는 환경변수 BOARD_AGENT를 지정하세요.")
    return check_name(name, "이름")


def _text(args: argparse.Namespace, *, required: bool = False, what: str = "내용") -> str:
    path = getattr(args, "file", None)
    value = getattr(args, "message", None)
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise BoardError(f"파일을 읽을 수 없습니다: {path} ({exc})") from exc
    elif value == "-":
        text = sys.stdin.read()
    else:
        text = value or ""
    if required and not text.strip():
        raise BoardError(f"{what}을(를) -m 또는 --file로 입력하세요.")
    return text


def _split(values: Optional[Sequence[str]]) -> list[str]:
    out: list[str] = []
    for value in values or ():
        out.extend(v.strip() for v in str(value).split(",") if v.strip())
    return out


def _emit(args: argparse.Namespace, data: Any, text: str) -> None:
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(text)


def _task_line(t: dict[str, Any]) -> str:
    flags = []
    if t["review_required"]:
        flags.append("검토필요")
    if t["stale"]:
        flags.append("정체")
    if t["waiting_on"]:
        flags.append("선행대기:" + ",".join(t["waiting_on"]))
    tail = f"  {' '.join(flags)}" if flags else ""
    return (
        f"{t['key']}  [{t['status_label']}] {t['title']}"
        f"  (단계 {t['phase'] or '-'}, {PRIORITY_LABELS.get(t['priority'], t['priority'])}, "
        f"담당 {t['assignee'] or '-'}){tail}"
    )


def _task_block(t: dict[str, Any]) -> str:
    lines = [
        f"{t['key']}  {t['title']}",
        f"  상태: {t['status_label']}   단계: {t['phase'] or '-'}   우선순위: "
        f"{PRIORITY_LABELS.get(t['priority'], t['priority'])}   담당: {t['assignee'] or '-'}",
        f"  검토 필요: {'예' if t['review_required'] else '아니오'}   라벨: {', '.join(t['labels']) or '-'}"
        f"   갱신(UTC): {t['updated_at']}" + ("   ※ 정체" if t["stale"] else ""),
    ]
    if t.get("depends_on"):
        deps = ", ".join(f"{d['key']}({STATUS_LABELS.get(d['status'], d['status'])})" for d in t["depends_on"])
        lines.append(f"  선행 과업: {deps}")
    if t.get("dependents"):
        lines.append("  후속 과업: " + ", ".join(d["key"] for d in t["dependents"]))
    if t["body"]:
        lines.append("")
        lines.extend("  " + line for line in t["body"].splitlines())
    if t.get("posts"):
        lines.append("")
        lines.append("  ── 기록 ──")
        for p in t["posts"]:
            lines.append(f"  [{p['created_at']}] {p['author']} · {KIND_LABELS.get(p['kind'], p['kind'])}")
            lines.extend("      " + line for line in p["body"].splitlines())
    return "\n".join(lines)


# --------------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    path = _db_path(args)
    existed = path.exists()
    owner = args.owner or os.environ.get("BOARD_OWNER")
    with Board.init(path, owner=owner) as board:
        owners = board.owners()
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# 에이전트 게시판 데이터는 저장소에 올리지 않는다\n*\n", encoding="utf-8")
    state = "이미 있는 게시판을 확인했습니다" if existed else "게시판을 만들었습니다"
    _emit(args, {"path": str(path), "owners": owners, "created": not existed},
          f"{state}: {path}\n소유자: {', '.join(owners)}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    try:
        data = json.loads(Path(args.seed_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BoardError(f"시드 파일을 읽을 수 없습니다: {args.seed_file} ({exc})") from exc
    with _open(args) as board:
        actor = args.actor or os.environ.get("BOARD_AGENT") or board.primary_owner()
        result = board.seed(check_name(actor, "이름"), data)
    text = f"새로 만든 과업 {len(result['created'])}개, 이미 있어 건너뛴 과업 {len(result['skipped'])}개"
    if result["created"]:
        text += "\n  " + ", ".join(result["created"])
    _emit(args, result, text)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    with _open(args) as board:
        task = board.add_task(
            _actor(args),
            args.title,
            body=_text(args),
            phase=args.phase or "",
            priority=args.priority,
            review_required=args.review,
            labels=_split(args.label),
            depends_on=_split(args.dep),
            status=args.status,
        )
    _emit(args, task, f"과업을 추가했습니다.\n{_task_line(task)}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    body = _text(args) if (args.message is not None or args.file) else None
    with _open(args) as board:
        task = board.edit_task(
            _actor(args),
            args.task,
            title=args.title,
            body=body,
            phase=args.phase,
            priority=args.priority,
            labels=_split(args.label) if args.label is not None else None,
            review_required=args.review,
            add_deps=_split(args.dep),
        )
    _emit(args, task, f"과업을 고쳤습니다.\n{_task_line(task)}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    statuses = _split(args.status)
    with _open(args) as board:
        assignee = args.assignee
        if args.mine:
            assignee = _actor(args)
        tasks = board.list_tasks(
            statuses=statuses or None,
            phase=args.phase,
            assignee=assignee,
            label=args.label,
            include_closed=args.all,
        )
    text = "\n".join(_task_line(t) for t in tasks) if tasks else "조건에 맞는 과업이 없습니다."
    _emit(args, tasks, text)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    with _open(args) as board:
        task = board.get_task(args.task)
    _emit(args, task, _task_block(task))
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    with _open(args) as board:
        result = board.next_task(_actor(args), phase=args.phase)
    lines = []
    if result["mine"]:
        lines.append("이미 맡고 있는 과업:")
        lines.extend("  " + _task_line(t) for t in result["mine"])
        lines.append("")
    if result["next"]:
        lines.append("다음으로 맡을 과업:")
        lines.append("  " + _task_line(result["next"]))
        lines.append(f"\n맡으려면: board claim {result['next']['key']}")
    else:
        lines.append("지금 맡을 수 있는 과업이 없습니다 (선행 과업 대기 또는 모두 배정됨).")
    _emit(args, result, "\n".join(lines))
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    with _open(args) as board:
        task = board.claim(_actor(args), args.task, steal=args.steal)
    _emit(args, task, f"과업을 맡았습니다.\n{_task_line(task)}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    with _open(args) as board:
        task = board.release(_actor(args), args.task, _text(args))
    _emit(args, task, f"과업을 내려놓았습니다.\n{_task_line(task)}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with _open(args) as board:
        task = board.set_status(_actor(args), args.task, args.status, _text(args))
    _emit(args, task, f"상태를 바꿨습니다.\n{_task_line(task)}")
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    with _open(args) as board:
        result = board.post(_actor(args), args.task, args.kind, _text(args, required=True))
    where = result["task"] or "게시판 전체"
    _emit(args, result, f"{where}에 {KIND_LABELS[result['kind']]} 글을 남겼습니다 (#{result['id']}).")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    with _open(args) as board:
        actor = _actor(args)
        if args.review_action == "request":
            task = board.request_review(actor, args.task, _text(args, required=True, what="검토 요청 내용"))
            text = f"검토를 요청했습니다. 소유자의 결정을 기다리세요.\n{_task_line(task)}"
        elif args.review_action == "approve":
            task = board.review(actor, args.task, "approve", _text(args))
            text = f"승인했습니다.\n{_task_line(task)}"
        else:
            task = board.review(actor, args.task, "changes", _text(args, required=True, what="수정 요청 내용"))
            text = f"수정을 요청했습니다.\n{_task_line(task)}"
    _emit(args, task, text)
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    with _open(args) as board:
        result = board.heartbeat(_actor(args), args.task)
    _emit(args, result, f"생존 신호를 남겼습니다 ({result['task'] or '과업 없음'}).")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    with _open(args) as board:
        s = board.summary()
    counts = "  ".join(f"{STATUS_LABELS[k]} {v}" for k, v in s["counts"].items())
    lines = [f"게시판: {s['board']}", f"소유자: {', '.join(s['owners'])}", counts, ""]
    for title, items in (
        ("검토 대기", s["review_queue"]),
        ("막힘", s["blocked"]),
        (f"정체 ({s['stale_minutes']}분 이상 갱신 없음)", s["stale"]),
    ):
        lines.append(f"{title}: {len(items)}건")
        lines.extend("  " + _task_line(t) for t in items)
    lines.append("")
    lines.append("에이전트:")
    if not s["agents"]:
        lines.append("  (아직 없음)")
    for a in s["agents"]:
        role = " (소유자)" if a["owner"] else ""
        lines.append(f"  {a['name']}{role} · {a['minutes_ago']}분 전 · 현재 과업 {a['current_task'] or '-'}")
    _emit(args, s, "\n".join(lines))
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    with _open(args) as board:
        agents = board.agents()
    text = "\n".join(
        f"{a['name']}{' (소유자)' if a['owner'] else ''} · {a['minutes_ago']}분 전 · 현재 과업 {a['current_task'] or '-'}"
        for a in agents
    ) or "아직 활동한 에이전트가 없습니다."
    _emit(args, agents, text)
    return 0


def cmd_owner(args: argparse.Namespace) -> int:
    with _open(args) as board:
        actor = _actor(args)
        if not board.is_owner(actor):
            raise BoardError("소유자 목록은 현재 소유자만 바꿀 수 있습니다.")
        owners = board.set_owners(args.names)
    _emit(args, {"owners": owners}, f"소유자: {', '.join(owners)}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with _open(args) as board:
        text = board.export_markdown()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"스냅샷을 저장했습니다: {args.out}")
    else:
        print(text, end="")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .web import serve

    return serve(_db_path(args), host=args.host, port=args.port, open_browser=not args.no_browser)


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import run

    return run(_db_path(args))


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS, help="게시판 파일 경로 (기본: 저장소 루트의 .board/board.db)")
    common.add_argument("--as", dest="actor", default=argparse.SUPPRESS, help="작업하는 에이전트·사람 이름 (기본: $BOARD_AGENT)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="JSON으로 출력")

    parser = argparse.ArgumentParser(
        prog="board",
        description="AI 에이전트와 소유자가 함께 쓰는 로컬 프로젝트 게시판",
    )
    parser.add_argument("--db", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--as", dest="actor", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=False, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    def add(name: str, help_text: str, func: Any) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text, parents=[common])
        p.set_defaults(func=func)
        return p

    def text_options(p: argparse.ArgumentParser, help_text: str = "내용 ('-'이면 표준 입력)") -> None:
        p.add_argument("-m", "--message", default=None, help=help_text)
        p.add_argument("--file", default=None, help="내용을 담은 UTF-8 파일")

    p = add("init", "게시판을 만든다 (이미 있으면 확인만 한다)", cmd_init)
    p.add_argument("--owner", help="소유자(사람) 이름. 승인은 소유자만 할 수 있다 (기본: owner)")

    p = add("seed", "시드 JSON에서 과업을 가져온다 (같은 key는 건너뜀)", cmd_seed)
    p.add_argument("seed_file")

    p = add("add", "과업을 추가한다", cmd_add)
    p.add_argument("title")
    text_options(p, "본문 ('-'이면 표준 입력)")
    p.add_argument("--phase", help="단계 (예: P0, P1, E2)")
    p.add_argument("--priority", choices=PRIORITIES, default="medium")
    p.add_argument("--review", action="store_true", help="소유자 검토가 필요한 과업으로 표시")
    p.add_argument("--label", action="append", help="라벨 (여러 번 또는 쉼표로)")
    p.add_argument("--dep", action="append", help="선행 과업 (예: T-0001)")
    p.add_argument("--status", choices=("todo", "backlog"), default="todo")

    p = add("edit", "과업 내용을 고친다", cmd_edit)
    p.add_argument("task")
    p.add_argument("--title")
    text_options(p, "새 본문 ('-'이면 표준 입력)")
    p.add_argument("--phase")
    p.add_argument("--priority", choices=PRIORITIES)
    p.add_argument("--label", action="append", help="라벨 전체를 이 값으로 바꾼다")
    review = p.add_mutually_exclusive_group()
    review.add_argument("--review", dest="review", action="store_const", const=True, default=None)
    review.add_argument("--no-review", dest="review", action="store_const", const=False)
    p.add_argument("--dep", action="append", help="선행 과업 추가")

    p = add("list", "과업 목록 (기본: 진행 중인 것만)", cmd_list)
    p.add_argument("--status", action="append", help=f"상태 필터: {', '.join(STATUSES)}")
    p.add_argument("--phase")
    p.add_argument("--assignee")
    p.add_argument("--label")
    p.add_argument("--mine", action="store_true", help="내가 맡은 과업만")
    p.add_argument("--all", action="store_true", help="완료·취소 포함")

    p = add("show", "과업 상세와 기록", cmd_show)
    p.add_argument("task")

    p = add("next", "다음에 맡을 과업을 추천한다", cmd_next)
    p.add_argument("--phase")

    p = add("claim", "과업을 맡는다", cmd_claim)
    p.add_argument("task")
    p.add_argument("--steal", action="store_true", help="정체된 과업을 이어받는다")

    p = add("release", "맡은 과업을 내려놓는다", cmd_release)
    p.add_argument("task")
    text_options(p, "사유")

    p = add("status", "과업 상태를 바꾼다", cmd_status)
    p.add_argument("task")
    p.add_argument("status", choices=STATUSES)
    text_options(p, "메모 (막힘이면 필수)")

    p = add("post", "진행 보고·결정·질문 등을 남긴다", cmd_post)
    p.add_argument("task", nargs="?", default=None, help="과업 번호 (생략하면 게시판 전체 글)")
    p.add_argument("-k", "--kind", choices=USER_POST_KINDS, default="progress")
    text_options(p)

    p = add("review", "검토 요청(에이전트) 또는 승인·수정 요청(소유자)", cmd_review)
    p.add_argument("review_action", choices=("request", "approve", "changes"))
    p.add_argument("task")
    text_options(p, "검토 요청 내용 또는 검토 의견")

    p = add("heartbeat", "작업 중이라는 생존 신호를 남긴다", cmd_heartbeat)
    p.add_argument("task", nargs="?", default=None)

    add("summary", "게시판 요약 (검토 대기, 막힘, 정체, 에이전트)", cmd_summary)
    add("agents", "에이전트 활동 목록", cmd_agents)

    p = add("owner", "소유자 목록을 바꾼다 (소유자만)", cmd_owner)
    p.add_argument("owner_action", choices=("set",))
    p.add_argument("names", nargs="+")

    p = add("export", "Markdown 스냅샷을 만든다", cmd_export)
    p.add_argument("--out", help="저장할 파일 (생략하면 화면 출력)")

    p = add("serve", "로컬 웹 화면을 연다 (127.0.0.1 전용)", cmd_serve)
    p.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost", "::1"))
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않는다")

    add("mcp", "MCP 서버(stdio)로 실행한다 ('pip install mcp' 필요)", cmd_mcp)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BoardError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
