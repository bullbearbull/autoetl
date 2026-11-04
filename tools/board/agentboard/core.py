"""Agent Board core: a SQLite-backed project board shared by AI agents and a human owner.

Only the Python standard library is used, so the board works on any PC with Python 3.10+.
All rule checks live here; the CLI, the local web UI and the MCP server are thin layers on top.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

SCHEMA_VERSION = 1

STATUSES = ("backlog", "todo", "in_progress", "blocked", "review", "done", "cancelled")
CLOSED_STATUSES = ("done", "cancelled")
PRIORITIES = ("high", "medium", "low")
# Kinds an agent or the owner may post directly.
USER_POST_KINDS = ("progress", "decision", "question", "blocker", "handoff", "note")
# Kinds written by the board itself (status changes, review flow).
SYSTEM_POST_KINDS = ("status", "review_request", "review_result")
POST_KINDS = USER_POST_KINDS + SYSTEM_POST_KINDS

STATUS_LABELS = {
    "backlog": "보류",
    "todo": "할 일",
    "in_progress": "진행 중",
    "blocked": "막힘",
    "review": "검토 대기",
    "done": "완료",
    "cancelled": "취소",
}
PRIORITY_LABELS = {"high": "높음", "medium": "보통", "low": "낮음"}

MAX_TITLE = 200
MAX_BODY = 20_000
MAX_LABELS = 10
DEFAULT_STALE_MINUTES = 60
DEFAULT_OWNER = "owner"

_NAME_RE = re.compile(r"^[\w.@-]{1,64}$")
_LABEL_RE = re.compile(r"^[\w.:/-]{1,32}$")
_EXT_KEY_RE = re.compile(r"^[\w.-]{1,40}$")

_STATUS_ORDER = {s: i for i, s in enumerate(("review", "blocked", "in_progress", "todo", "backlog", "done", "cancelled"))}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ext_key         TEXT UNIQUE,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    phase           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'todo',
    priority        TEXT NOT NULL DEFAULT 'medium',
    assignee        TEXT,
    review_required INTEGER NOT NULL DEFAULT 0,
    labels          TEXT NOT NULL DEFAULT '',
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    claimed_at      TEXT,
    heartbeat_at    TEXT
);
CREATE TABLE IF NOT EXISTS deps (
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, depends_on)
);
CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    author     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    name         TEXT PRIMARY KEY,
    last_seen    TEXT NOT NULL,
    current_task INTEGER REFERENCES tasks(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    task_id INTEGER,
    detail  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_posts_task ON posts(task_id, id);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);
"""


class BoardError(Exception):
    """A rule violation or bad input. The message is shown to the user as-is."""


# --------------------------------------------------------------------------- helpers


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def task_key(task_id: int) -> str:
    return f"T-{task_id:04d}"


def parse_ref(ref: Any) -> int:
    """Accept 'T-0007', 't-7', '7' or 7 and return the numeric id."""
    text = str(ref).strip().upper()
    if text.startswith("T-"):
        text = text[2:]
    if not text.isdigit() or int(text) <= 0:
        raise BoardError(f"과업 번호 형식이 아닙니다: {ref!r} (예: T-0001)")
    return int(text)


def check_name(name: Optional[str], what: str = "이름") -> str:
    if not name or not _NAME_RE.match(name):
        raise BoardError(
            f"{what}이 올바르지 않습니다: {name!r} — 영문·숫자·한글과 . _ - @ 만 1~64자로 쓸 수 있습니다."
        )
    return name


def _clean_text(value: Optional[str], *, field: str, limit: int, required: bool) -> str:
    text = (value or "").replace("\r\n", "\n").strip()
    if required and not text:
        raise BoardError(f"{field}을(를) 입력하세요.")
    if len(text) > limit:
        raise BoardError(f"{field}이(가) 너무 깁니다 ({len(text)}자, 최대 {limit}자).")
    return text


def _clean_labels(labels: Optional[Iterable[str]]) -> str:
    cleaned: list[str] = []
    for raw in labels or ():
        for part in str(raw).split(","):
            label = part.strip()
            if not label:
                continue
            if not _LABEL_RE.match(label):
                raise BoardError(f"라벨 형식이 올바르지 않습니다: {label!r}")
            if label not in cleaned:
                cleaned.append(label)
    if len(cleaned) > MAX_LABELS:
        raise BoardError(f"라벨은 최대 {MAX_LABELS}개까지 붙일 수 있습니다.")
    return ",".join(cleaned)


def find_default_db(cwd: Optional[Path] = None) -> Path:
    """Where the board lives when no path is given.

    1. $BOARD_DB if set.
    2. <main worktree root>/.board/board.db when inside a git repository, so every
       linked worktree of the same repository shares one board.
    3. The nearest parent directory that already has a .board folder, else ./.board.
    """
    env = os.environ.get("BOARD_DB")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(cwd or Path.cwd()).resolve()
    root = _git_main_root(here)
    if root is None:
        for candidate in (here, *here.parents):
            if (candidate / ".board").is_dir():
                root = candidate
                break
        else:
            root = here
    return root / ".board" / "board.db"


def _git_main_root(cwd: Path) -> Optional[Path]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    common = Path(out.stdout.strip())
    if not common.is_absolute():
        common = (cwd / common).resolve()
    # Normal repository: <root>/.git -> <root>. Bare repository: keep the repo dir itself.
    return common.parent if common.name == ".git" else common


# --------------------------------------------------------------------------- board


class Board:
    """One board = one SQLite file. Open a new Board per thread/process."""

    def __init__(
        self,
        path: str | Path,
        *,
        create: bool = False,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        if not create and not self.path.exists():
            raise BoardError(
                f"게시판 파일이 없습니다: {self.path}\n먼저 'board init'으로 게시판을 만드세요."
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass  # e.g. network drives without shared memory support; default journal still works
        if create:
            self._create_schema()
        else:
            self._check_schema()

    # ----------------------------------------------------------------- lifecycle

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Board":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @classmethod
    def init(cls, path: str | Path, owner: Optional[str] = None) -> "Board":
        board = cls(path, create=True)
        if owner:
            board.set_owners([owner])
        return board

    def _create_schema(self) -> None:
        with self._tx():
            for statement in _SCHEMA.strip().split(";"):
                if statement.strip():
                    self.conn.execute(statement)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('owners', ?)",
                (json.dumps([DEFAULT_OWNER]),),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('stale_minutes', ?)",
                (str(DEFAULT_STALE_MINUTES),),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)",
                (self._now(),),
            )

    def _check_schema(self) -> None:
        try:
            row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise BoardError(f"게시판 파일을 읽을 수 없습니다: {self.path} ({exc})") from exc
        if row is None:
            raise BoardError(f"게시판 파일이 아닙니다: {self.path}")
        if int(row["value"]) > SCHEMA_VERSION:
            raise BoardError("이 게시판은 더 새로운 버전의 도구로 만들어졌습니다. 도구를 업데이트하세요.")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # ----------------------------------------------------------------- meta

    def _now(self) -> str:
        return to_iso(self._clock())

    def _meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def owners(self) -> list[str]:
        try:
            value = json.loads(self._meta("owners", "[]") or "[]")
        except json.JSONDecodeError:
            value = []
        return [str(v) for v in value] or [DEFAULT_OWNER]

    def primary_owner(self) -> str:
        return self.owners()[0]

    def set_owners(self, owners: Sequence[str]) -> list[str]:
        cleaned = [check_name(o, "소유자 이름") for o in owners if o]
        if not cleaned:
            raise BoardError("소유자를 한 명 이상 지정하세요.")
        with self._tx():
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES ('owners', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(cleaned),),
            )
        return cleaned

    def is_owner(self, actor: str) -> bool:
        return actor in self.owners()

    def stale_minutes(self) -> int:
        try:
            return max(1, int(self._meta("stale_minutes", str(DEFAULT_STALE_MINUTES)) or DEFAULT_STALE_MINUTES))
        except ValueError:
            return DEFAULT_STALE_MINUTES

    # ----------------------------------------------------------------- internals

    def _row(self, task_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise BoardError(f"없는 과업입니다: {task_key(task_id)}")
        return row

    def _is_stale(self, row: sqlite3.Row) -> bool:
        if row["status"] != "in_progress" or not row["heartbeat_at"]:
            return False
        age = self._clock() - from_iso(row["heartbeat_at"])
        return age > timedelta(minutes=self.stale_minutes())

    def _unmet_deps(self, task_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT t.* FROM deps d JOIN tasks t ON t.id = d.depends_on "
            "WHERE d.task_id=? AND t.status NOT IN ('done', 'cancelled') ORDER BY t.id",
            (task_id,),
        ).fetchall()

    def _event(self, actor: str, action: str, task_id: Optional[int], **detail: Any) -> None:
        self.conn.execute(
            "INSERT INTO events(ts, actor, action, task_id, detail) VALUES (?, ?, ?, ?, ?)",
            (self._now(), actor, action, task_id, json.dumps(detail, ensure_ascii=False)),
        )

    def _post(self, task_id: Optional[int], author: str, kind: str, body: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO posts(task_id, author, kind, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, author, kind, body, self._now()),
        )
        return int(cur.lastrowid)

    def _touch(self, actor: str, current_task: Any = ...) -> None:
        now = self._now()
        if current_task is ...:
            self.conn.execute(
                "INSERT INTO agents(name, last_seen) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen",
                (actor, now),
            )
        else:
            self.conn.execute(
                "INSERT INTO agents(name, last_seen, current_task) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen, "
                "current_task=excluded.current_task",
                (actor, now, current_task),
            )

    def _clear_current(self, task_id: int) -> None:
        self.conn.execute("UPDATE agents SET current_task=NULL WHERE current_task=?", (task_id,))

    def _task_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        unmet = self._unmet_deps(row["id"])
        return {
            "key": task_key(row["id"]),
            "id": row["id"],
            "ext_key": row["ext_key"],
            "title": row["title"],
            "body": row["body"],
            "phase": row["phase"],
            "status": row["status"],
            "status_label": STATUS_LABELS.get(row["status"], row["status"]),
            "priority": row["priority"],
            "assignee": row["assignee"],
            "review_required": bool(row["review_required"]),
            "labels": [x for x in row["labels"].split(",") if x],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "claimed_at": row["claimed_at"],
            "heartbeat_at": row["heartbeat_at"],
            "stale": self._is_stale(row),
            "waiting_on": [task_key(d["id"]) for d in unmet],
        }

    def _resolve_deps(self, task_id: Optional[int], refs: Iterable[Any]) -> list[int]:
        ids: list[int] = []
        for ref in refs or ():
            if ref in (None, ""):
                continue
            dep_id = parse_ref(ref)
            self._row(dep_id)
            if task_id is not None and dep_id == task_id:
                raise BoardError("과업이 자기 자신을 선행 과업으로 가질 수 없습니다.")
            if dep_id not in ids:
                ids.append(dep_id)
        if task_id is not None:
            for dep_id in ids:
                if self._reaches(dep_id, task_id):
                    raise BoardError(f"순환 의존이 생깁니다: {task_key(dep_id)} → {task_key(task_id)}")
        return ids

    def _reaches(self, start: int, target: int) -> bool:
        """True if `start` (transitively) depends on `target`."""
        seen: set[int] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(
                r["depends_on"]
                for r in self.conn.execute("SELECT depends_on FROM deps WHERE task_id=?", (current,))
            )
        return False

    # ----------------------------------------------------------------- tasks

    def add_task(
        self,
        actor: str,
        title: str,
        *,
        body: str = "",
        phase: str = "",
        priority: str = "medium",
        review_required: bool = False,
        labels: Optional[Iterable[str]] = None,
        depends_on: Optional[Iterable[Any]] = None,
        status: str = "todo",
        ext_key: Optional[str] = None,
    ) -> dict[str, Any]:
        check_name(actor, "작성자 이름")
        title = _clean_text(title, field="제목", limit=MAX_TITLE, required=True)
        body = _clean_text(body, field="본문", limit=MAX_BODY, required=False)
        phase = _clean_text(phase, field="단계", limit=20, required=False)
        if priority not in PRIORITIES:
            raise BoardError(f"우선순위는 {', '.join(PRIORITIES)} 중 하나입니다.")
        if status not in ("backlog", "todo"):
            raise BoardError("새 과업의 상태는 backlog 또는 todo만 됩니다.")
        if ext_key is not None and not _EXT_KEY_RE.match(ext_key):
            raise BoardError(f"외부 키 형식이 올바르지 않습니다: {ext_key!r}")
        label_text = _clean_labels(labels)
        now = self._now()
        with self._tx():
            dep_ids = self._resolve_deps(None, depends_on or ())
            cur = self.conn.execute(
                "INSERT INTO tasks(ext_key, title, body, phase, status, priority, review_required, labels, "
                "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ext_key, title, body, phase, status, priority, int(bool(review_required)), label_text,
                 actor, now, now),
            )
            task_id = int(cur.lastrowid)
            for dep_id in dep_ids:
                self.conn.execute("INSERT INTO deps(task_id, depends_on) VALUES (?, ?)", (task_id, dep_id))
            self._event(actor, "task.add", task_id, title=title)
            self._touch(actor)
        return self.get_task(task_id, with_posts=False)

    def edit_task(
        self,
        actor: str,
        ref: Any,
        *,
        title: Optional[str] = None,
        body: Optional[str] = None,
        phase: Optional[str] = None,
        priority: Optional[str] = None,
        labels: Optional[Iterable[str]] = None,
        review_required: Optional[bool] = None,
        add_deps: Optional[Iterable[Any]] = None,
    ) -> dict[str, Any]:
        check_name(actor, "작성자 이름")
        task_id = parse_ref(ref)
        with self._tx():
            row = self._row(task_id)
            owner = self.is_owner(actor)
            if not owner and actor not in (row["assignee"], row["created_by"]):
                raise BoardError("과업은 담당자, 작성자, 소유자만 고칠 수 있습니다.")
            changes: dict[str, Any] = {}
            if title is not None:
                changes["title"] = _clean_text(title, field="제목", limit=MAX_TITLE, required=True)
            if body is not None:
                changes["body"] = _clean_text(body, field="본문", limit=MAX_BODY, required=False)
            if phase is not None:
                changes["phase"] = _clean_text(phase, field="단계", limit=20, required=False)
            if priority is not None:
                if priority not in PRIORITIES:
                    raise BoardError(f"우선순위는 {', '.join(PRIORITIES)} 중 하나입니다.")
                changes["priority"] = priority
            if labels is not None:
                changes["labels"] = _clean_labels(labels)
            if review_required is not None:
                if not review_required and row["review_required"] and not owner:
                    raise BoardError("검토 필요 표시는 소유자만 해제할 수 있습니다.")
                changes["review_required"] = int(bool(review_required))
            dep_ids = self._resolve_deps(task_id, add_deps or ())
            if not changes and not dep_ids:
                raise BoardError("바꿀 내용이 없습니다.")
            if changes:
                changes["updated_at"] = self._now()
                cols = ", ".join(f"{k}=?" for k in changes)
                self.conn.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*changes.values(), task_id))
            for dep_id in dep_ids:
                self.conn.execute(
                    "INSERT OR IGNORE INTO deps(task_id, depends_on) VALUES (?, ?)", (task_id, dep_id)
                )
            fields = sorted(k for k in changes if k != "updated_at")
            if dep_ids:
                fields.append("deps")
            self._event(actor, "task.edit", task_id, fields=fields)
            self._touch(actor)
        return self.get_task(task_id, with_posts=False)

    def list_tasks(
        self,
        *,
        statuses: Optional[Sequence[str]] = None,
        phase: Optional[str] = None,
        assignee: Optional[str] = None,
        label: Optional[str] = None,
        include_closed: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if statuses:
            bad = [s for s in statuses if s not in STATUSES]
            if bad:
                raise BoardError(f"알 수 없는 상태: {', '.join(bad)}")
            clauses.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        elif not include_closed:
            clauses.append("status NOT IN ('done', 'cancelled')")
        if phase:
            clauses.append("phase=?")
            params.append(phase)
        if assignee:
            clauses.append("assignee=?")
            params.append(assignee)
        if label:
            clauses.append("(',' || labels || ',') LIKE ?")
            params.append(f"%,{label},%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM tasks {where}", params).fetchall()
        rows.sort(key=lambda r: (_STATUS_ORDER.get(r["status"], 99), _PRIORITY_ORDER.get(r["priority"], 9), r["id"]))
        return [self._task_dict(r) for r in rows]

    def get_task(self, ref: Any, *, with_posts: bool = True) -> dict[str, Any]:
        task_id = parse_ref(ref)
        row = self._row(task_id)
        data = self._task_dict(row)
        deps = self.conn.execute(
            "SELECT t.id, t.title, t.status FROM deps d JOIN tasks t ON t.id=d.depends_on "
            "WHERE d.task_id=? ORDER BY t.id",
            (task_id,),
        ).fetchall()
        dependents = self.conn.execute(
            "SELECT t.id, t.title, t.status FROM deps d JOIN tasks t ON t.id=d.task_id "
            "WHERE d.depends_on=? ORDER BY t.id",
            (task_id,),
        ).fetchall()
        data["depends_on"] = [
            {"key": task_key(d["id"]), "title": d["title"], "status": d["status"]} for d in deps
        ]
        data["dependents"] = [
            {"key": task_key(d["id"]), "title": d["title"], "status": d["status"]} for d in dependents
        ]
        if with_posts:
            data["posts"] = self.posts(task_id)
        return data

    def posts(self, ref: Any = None, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        if ref is None:
            sql = "SELECT * FROM posts ORDER BY id DESC"
            params: tuple[Any, ...] = ()
            if limit:
                sql += " LIMIT ?"
                params = (int(limit),)
            rows = list(reversed(self.conn.execute(sql, params).fetchall()))
        else:
            task_id = parse_ref(ref)
            rows = self.conn.execute(
                "SELECT * FROM posts WHERE task_id=? ORDER BY id", (task_id,)
            ).fetchall()
        return [
            {
                "id": r["id"],
                "task": task_key(r["task_id"]) if r["task_id"] else None,
                "author": r["author"],
                "kind": r["kind"],
                "body": r["body"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ----------------------------------------------------------------- workflow

    def claim(self, actor: str, ref: Any, *, steal: bool = False) -> dict[str, Any]:
        check_name(actor, "에이전트 이름")
        task_id = parse_ref(ref)
        with self._tx():
            row = self._row(task_id)
            if row["status"] in CLOSED_STATUSES:
                raise BoardError(f"{task_key(task_id)}은(는) 이미 종료된 과업입니다 ({STATUS_LABELS[row['status']]}).")
            if row["status"] == "review":
                raise BoardError(f"{task_key(task_id)}은(는) 소유자 검토를 기다리는 중입니다.")
            previous = row["assignee"]
            if previous and previous != actor:
                if not steal:
                    raise BoardError(f"{task_key(task_id)}은(는) {previous}이(가) 맡고 있습니다.")
                if not self._is_stale(row):
                    raise BoardError(
                        f"{previous}의 마지막 갱신이 {self.stale_minutes()}분 이내라서 가져올 수 없습니다."
                    )
            unmet = self._unmet_deps(task_id)
            if unmet:
                keys = ", ".join(task_key(d["id"]) for d in unmet)
                raise BoardError(f"선행 과업이 끝나지 않았습니다: {keys}")
            now = self._now()
            claimed_at = row["claimed_at"] if previous == actor and row["claimed_at"] else now
            self.conn.execute(
                "UPDATE tasks SET status='in_progress', assignee=?, claimed_at=?, heartbeat_at=?, updated_at=? "
                "WHERE id=?",
                (actor, claimed_at, now, now, task_id),
            )
            if previous and previous != actor:
                message = f"정체된 과업을 이어받았습니다 (이전 담당: {previous})."
            elif previous == actor:
                message = "과업을 다시 이어서 진행합니다."
            else:
                message = "과업을 맡았습니다."
            self._post(task_id, actor, "status", message)
            self._event(actor, "task.claim", task_id, previous=previous)
            if previous and previous != actor:
                self.conn.execute(
                    "UPDATE agents SET current_task=NULL WHERE name=? AND current_task=?", (previous, task_id)
                )
            self._touch(actor, task_id)
        return self.get_task(task_id, with_posts=False)

    def release(self, actor: str, ref: Any, note: str = "") -> dict[str, Any]:
        check_name(actor, "에이전트 이름")
        task_id = parse_ref(ref)
        note = _clean_text(note, field="메모", limit=MAX_BODY, required=False)
        with self._tx():
            row = self._row(task_id)
            if row["status"] not in ("in_progress", "blocked"):
                raise BoardError(f"진행 중이거나 막힌 과업만 내려놓을 수 있습니다 (현재: {STATUS_LABELS[row['status']]}).")
            if row["assignee"] != actor and not self.is_owner(actor):
                raise BoardError("담당자 또는 소유자만 과업을 내려놓을 수 있습니다.")
            now = self._now()
            self.conn.execute(
                "UPDATE tasks SET status='todo', assignee=NULL, heartbeat_at=NULL, updated_at=? WHERE id=?",
                (now, task_id),
            )
            message = "과업을 내려놓았습니다." + (f"\n{note}" if note else "")
            self._post(task_id, actor, "status", message)
            self._event(actor, "task.release", task_id, previous=row["assignee"])
            self._clear_current(task_id)
            self._touch(actor)
        return self.get_task(task_id, with_posts=False)

    def set_status(self, actor: str, ref: Any, status: str, note: str = "") -> dict[str, Any]:
        check_name(actor, "이름")
        task_id = parse_ref(ref)
        if status not in STATUSES:
            raise BoardError(f"알 수 없는 상태입니다: {status} (가능: {', '.join(STATUSES)})")
        note = _clean_text(note, field="메모", limit=MAX_BODY, required=False)
        if status == "review":
            raise BoardError("검토 요청은 'review request' 명령(또는 request_review 도구)을 쓰세요.")
        if status in ("todo", "backlog"):
            # Going back to the queue from active work is a release; otherwise a plain move.
            row = self._row(task_id)
            if row["status"] in ("in_progress", "blocked") and status == "todo":
                return self.release(actor, ref, note)
        with self._tx():
            row = self._row(task_id)
            old = row["status"]
            if old == status:
                raise BoardError(f"이미 '{STATUS_LABELS[status]}' 상태입니다.")
            owner = self.is_owner(actor)
            if not owner:
                if status == "cancelled":
                    raise BoardError("취소는 소유자만 할 수 있습니다. 필요하면 question 글로 제안하세요.")
                if old in CLOSED_STATUSES:
                    raise BoardError("종료된 과업은 소유자만 다시 열 수 있습니다.")
                if old == "review":
                    raise BoardError("검토 대기 중인 과업의 상태는 소유자가 결정합니다.")
                if row["assignee"] != actor:
                    if status == "in_progress":
                        raise BoardError("과업을 시작하려면 'claim'으로 먼저 맡으세요.")
                    raise BoardError("담당자만 상태를 바꿀 수 있습니다.")
                if status == "done" and row["review_required"]:
                    raise BoardError(
                        "이 과업은 소유자 검토가 필요합니다. 'review request'로 검토를 요청하세요."
                    )
                if status == "blocked" and not note:
                    raise BoardError("막힘으로 바꿀 때는 이유(-m)를 적으세요.")
                if status == "backlog":
                    raise BoardError("보류로 돌리는 것은 소유자만 할 수 있습니다. 'release'로 내려놓으세요.")
            now = self._now()
            assignee = row["assignee"]
            if status in ("todo", "backlog"):
                assignee = None
            self.conn.execute(
                "UPDATE tasks SET status=?, assignee=?, updated_at=?, heartbeat_at=? WHERE id=?",
                (status, assignee, now, now if status == "in_progress" else row["heartbeat_at"], task_id),
            )
            kind = "blocker" if status == "blocked" else "status"
            message = f"상태 변경: {STATUS_LABELS[old]} → {STATUS_LABELS[status]}" + (f"\n{note}" if note else "")
            self._post(task_id, actor, kind, message)
            self._event(actor, "task.status", task_id, old=old, new=status)
            if status in CLOSED_STATUSES or status in ("todo", "backlog"):
                self._clear_current(task_id)
            self._touch(actor)
        return self.get_task(task_id, with_posts=False)

    def post(self, actor: str, ref: Any, kind: str, body: str) -> dict[str, Any]:
        check_name(actor, "작성자 이름")
        if kind not in USER_POST_KINDS:
            raise BoardError(f"글 종류는 {', '.join(USER_POST_KINDS)} 중 하나입니다.")
        body = _clean_text(body, field="내용", limit=MAX_BODY, required=True)
        task_id = parse_ref(ref) if ref not in (None, "") else None
        with self._tx():
            if task_id is not None:
                row = self._row(task_id)
                if row["assignee"] == actor and row["status"] in ("in_progress", "blocked"):
                    self.conn.execute(
                        "UPDATE tasks SET heartbeat_at=?, updated_at=? WHERE id=?",
                        (self._now(), self._now(), task_id),
                    )
            post_id = self._post(task_id, actor, kind, body)
            self._event(actor, "post.add", task_id, kind=kind, post=post_id)
            self._touch(actor)
        return {"id": post_id, "task": task_key(task_id) if task_id else None, "kind": kind}

    def request_review(self, actor: str, ref: Any, summary: str) -> dict[str, Any]:
        check_name(actor, "에이전트 이름")
        task_id = parse_ref(ref)
        summary = _clean_text(summary, field="검토 요청 내용", limit=MAX_BODY, required=True)
        with self._tx():
            row = self._row(task_id)
            if row["status"] not in ("in_progress", "blocked"):
                raise BoardError(
                    f"진행 중인 과업만 검토를 요청할 수 있습니다 (현재: {STATUS_LABELS[row['status']]})."
                )
            if row["assignee"] != actor:
                raise BoardError("담당자만 검토를 요청할 수 있습니다.")
            now = self._now()
            self.conn.execute(
                "UPDATE tasks SET status='review', heartbeat_at=?, updated_at=? WHERE id=?",
                (now, now, task_id),
            )
            self._post(task_id, actor, "review_request", summary)
            self._event(actor, "review.request", task_id)
            self._touch(actor)
        return self.get_task(task_id, with_posts=False)

    def review(self, actor: str, ref: Any, decision: str, comment: str = "") -> dict[str, Any]:
        check_name(actor, "검토자 이름")
        task_id = parse_ref(ref)
        if not self.is_owner(actor):
            raise BoardError("승인과 수정 요청은 소유자만 할 수 있습니다.")
        if decision not in ("approve", "changes"):
            raise BoardError("검토 결정은 approve 또는 changes입니다.")
        comment = _clean_text(comment, field="검토 의견", limit=MAX_BODY, required=decision == "changes")
        with self._tx():
            row = self._row(task_id)
            if row["status"] != "review":
                raise BoardError(f"검토 대기 상태가 아닙니다 (현재: {STATUS_LABELS[row['status']]}).")
            now = self._now()
            if decision == "approve":
                self.conn.execute("UPDATE tasks SET status='done', updated_at=? WHERE id=?", (now, task_id))
                message = "승인했습니다." + (f"\n{comment}" if comment else "")
                self._clear_current(task_id)
            else:
                self.conn.execute(
                    "UPDATE tasks SET status='in_progress', heartbeat_at=?, updated_at=? WHERE id=?",
                    (now, now, task_id),
                )
                message = f"수정을 요청했습니다.\n{comment}"
            self._post(task_id, actor, "review_result", message)
            self._event(actor, f"review.{decision}", task_id)
            self._touch(actor)
        return self.get_task(task_id, with_posts=False)

    def heartbeat(self, actor: str, ref: Any = None) -> dict[str, Any]:
        check_name(actor, "에이전트 이름")
        task_id = parse_ref(ref) if ref not in (None, "") else None
        with self._tx():
            if task_id is not None:
                row = self._row(task_id)
                if row["assignee"] != actor:
                    raise BoardError("자기가 맡은 과업에만 생존 신호를 보낼 수 있습니다.")
                self.conn.execute("UPDATE tasks SET heartbeat_at=? WHERE id=?", (self._now(), task_id))
                self._touch(actor, task_id)
            else:
                self._touch(actor)
        return {"agent": actor, "task": task_key(task_id) if task_id else None, "at": self._now()}

    def next_task(self, actor: str, *, phase: Optional[str] = None) -> dict[str, Any]:
        """The best unclaimed task for `actor`, plus what the actor already holds."""
        check_name(actor, "에이전트 이름")
        mine = self.list_tasks(statuses=["in_progress", "blocked"], assignee=actor)
        candidates = [
            t
            for t in self.list_tasks(statuses=["todo"], phase=phase or None)
            if t["assignee"] is None and not t["waiting_on"]
        ]
        with self._tx():
            self._touch(actor)
        return {"next": candidates[0] if candidates else None, "mine": mine}

    # ----------------------------------------------------------------- overview

    def agents(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM agents ORDER BY last_seen DESC").fetchall()
        now = self._clock()
        result = []
        for r in rows:
            last = from_iso(r["last_seen"])
            result.append(
                {
                    "name": r["name"],
                    "last_seen": r["last_seen"],
                    "minutes_ago": int((now - last).total_seconds() // 60),
                    "current_task": task_key(r["current_task"]) if r["current_task"] else None,
                    "owner": self.is_owner(r["name"]),
                }
            )
        return result

    def summary(self) -> dict[str, Any]:
        counts = {s: 0 for s in STATUSES}
        for r in self.conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
            counts[r["status"]] = r["n"]
        active = self.list_tasks(statuses=["in_progress"])
        return {
            "board": str(self.path),
            "owners": self.owners(),
            "stale_minutes": self.stale_minutes(),
            "counts": counts,
            "review_queue": self.list_tasks(statuses=["review"]),
            "blocked": self.list_tasks(statuses=["blocked"]),
            "in_progress": active,
            "stale": [t for t in active if t["stale"]],
            "agents": self.agents(),
            "recent_posts": self.posts(limit=10),
        }

    def export_markdown(self) -> str:
        def cell(text: Any) -> str:
            return str(text or "").replace("|", "\\|").replace("\n", " ")

        lines = [
            "# 에이전트 게시판 스냅샷",
            "",
            f"기준 시각(UTC): {self._now()}",
            "",
        ]
        summary = self.summary()
        lines.append("| 상태 | 과업 수 |")
        lines.append("| --- | --- |")
        for status in STATUSES:
            lines.append(f"| {STATUS_LABELS[status]} | {summary['counts'][status]} |")
        lines.append("")
        tasks = self.list_tasks(include_closed=True)
        for status in ("review", "blocked", "in_progress", "todo", "backlog", "done", "cancelled"):
            group = [t for t in tasks if t["status"] == status]
            if not group:
                continue
            lines.append(f"## {STATUS_LABELS[status]} ({len(group)})")
            lines.append("")
            lines.append("| 과업 | 제목 | 단계 | 우선순위 | 담당 | 검토 필요 | 갱신(UTC) |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for t in group:
                lines.append(
                    f"| {t['key']} | {cell(t['title'])} | {cell(t['phase'])} | "
                    f"{PRIORITY_LABELS.get(t['priority'], t['priority'])} | {cell(t['assignee'] or '-')} | "
                    f"{'예' if t['review_required'] else '-'} | {t['updated_at']} |"
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    # ----------------------------------------------------------------- seed

    def seed(self, actor: str, data: dict[str, Any]) -> dict[str, Any]:
        """Import tasks from a seed document. Tasks whose `key` already exists are skipped.

        Seed format: {"tasks": [{"key", "title", "body", "phase", "priority", "review",
        "labels", "depends_on": [keys], "status", "assignee", "posts": [{"author", "kind", "body"}]}]}
        """
        check_name(actor, "작성자 이름")
        items = data.get("tasks")
        if not isinstance(items, list) or not items:
            raise BoardError("시드 파일에 tasks 목록이 없습니다.")
        created: list[str] = []
        skipped: list[str] = []
        with self._tx():
            key_to_id: dict[str, int] = {
                r["ext_key"]: r["id"]
                for r in self.conn.execute("SELECT id, ext_key FROM tasks WHERE ext_key IS NOT NULL")
            }
            pending_keys = {str(i.get("key")) for i in items if isinstance(i, dict)}
            for index, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    raise BoardError(f"{index}번째 항목이 객체가 아닙니다.")
                ext_key = str(item.get("key") or "")
                if not _EXT_KEY_RE.match(ext_key):
                    raise BoardError(f"{index}번째 항목의 key가 올바르지 않습니다: {ext_key!r}")
                if ext_key in key_to_id:
                    skipped.append(ext_key)
                    continue
                title = _clean_text(item.get("title"), field=f"{ext_key} 제목", limit=MAX_TITLE, required=True)
                body = _clean_text(item.get("body"), field=f"{ext_key} 본문", limit=MAX_BODY, required=False)
                phase = _clean_text(item.get("phase"), field=f"{ext_key} 단계", limit=20, required=False)
                priority = item.get("priority", "medium")
                if priority not in PRIORITIES:
                    raise BoardError(f"{ext_key}: 우선순위가 올바르지 않습니다: {priority!r}")
                status = item.get("status", "todo")
                if status not in STATUSES or status in CLOSED_STATUSES:
                    raise BoardError(f"{ext_key}: 시드 상태는 종료 상태가 될 수 없습니다: {status!r}")
                assignee = item.get("assignee")
                if assignee is not None:
                    check_name(assignee, f"{ext_key} 담당자")
                if status in ("in_progress", "blocked", "review") and not assignee:
                    raise BoardError(f"{ext_key}: {status} 상태에는 담당자가 필요합니다.")
                if status in ("todo", "backlog") and assignee:
                    raise BoardError(f"{ext_key}: 할 일·보류 상태에는 담당자를 둘 수 없습니다.")
                dep_ids: list[int] = []
                for dep in item.get("depends_on", []) or []:
                    dep = str(dep)
                    if dep not in key_to_id:
                        hint = " (시드 안에서는 앞쪽에 있어야 합니다)" if dep in pending_keys else ""
                        raise BoardError(f"{ext_key}: 선행 과업 키를 찾을 수 없습니다: {dep}{hint}")
                    dep_ids.append(key_to_id[dep])
                now = self._now()
                cur = self.conn.execute(
                    "INSERT INTO tasks(ext_key, title, body, phase, status, priority, assignee, review_required, "
                    "labels, created_by, created_at, updated_at, claimed_at, heartbeat_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ext_key, title, body, phase, status, priority, assignee,
                        int(bool(item.get("review", False))), _clean_labels(item.get("labels")),
                        actor, now, now,
                        now if assignee else None, now if assignee else None,
                    ),
                )
                task_id = int(cur.lastrowid)
                key_to_id[ext_key] = task_id
                for dep_id in dep_ids:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO deps(task_id, depends_on) VALUES (?, ?)", (task_id, dep_id)
                    )
                for post in item.get("posts", []) or []:
                    if not isinstance(post, dict):
                        raise BoardError(f"{ext_key}: posts 항목이 객체가 아닙니다.")
                    kind = post.get("kind", "note")
                    if kind not in POST_KINDS:
                        raise BoardError(f"{ext_key}: 글 종류가 올바르지 않습니다: {kind!r}")
                    author = check_name(post.get("author") or actor, f"{ext_key} 글 작성자")
                    text = _clean_text(post.get("body"), field=f"{ext_key} 글 내용", limit=MAX_BODY, required=True)
                    self._post(task_id, author, kind, text)
                    self._touch(author)
                if assignee and status in ("in_progress", "blocked"):
                    self._touch(assignee, task_id)
                self._event(actor, "task.seed", task_id, ext_key=ext_key)
                created.append(task_key(task_id))
            self._touch(actor)
        return {"created": created, "skipped": skipped}
