import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentboard.core import Board, BoardError, find_default_db  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "seed" / "p0.json"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)


class CoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / ".board" / "board.db"
        Board.init(self.path, owner="boss").close()
        self.clock = Clock()
        self.board = Board(self.path, clock=self.clock)

    def tearDown(self) -> None:
        self.board.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ basics

    def test_init_is_idempotent_and_keeps_owner(self) -> None:
        Board.init(self.path).close()
        with Board(self.path) as b:
            self.assertEqual(b.owners(), ["boss"])

    def test_missing_board_gives_clear_error(self) -> None:
        with self.assertRaises(BoardError) as ctx:
            Board(Path(self.tmp) / "nope.db")
        self.assertIn("board init", str(ctx.exception))

    def test_add_list_show(self) -> None:
        t = self.board.add_task("agent-a", "첫 과업", body="본문", phase="P0", priority="high", labels=["a,b", "c"])
        self.assertEqual(t["key"], "T-0001")
        self.assertEqual(t["labels"], ["a", "b", "c"])
        self.assertEqual(t["status"], "todo")
        listed = self.board.list_tasks()
        self.assertEqual([x["key"] for x in listed], ["T-0001"])
        shown = self.board.get_task("t-1")
        self.assertEqual(shown["title"], "첫 과업")
        self.assertEqual(shown["posts"], [])

    def test_input_validation(self) -> None:
        with self.assertRaises(BoardError):
            self.board.add_task("agent-a", "   ")
        with self.assertRaises(BoardError):
            self.board.add_task("bad name!", "제목")
        with self.assertRaises(BoardError):
            self.board.add_task("agent-a", "제목", priority="urgent")
        with self.assertRaises(BoardError):
            self.board.add_task("agent-a", "제목", status="done")
        with self.assertRaises(BoardError):
            self.board.add_task("agent-a", "x" * 201)
        with self.assertRaises(BoardError):
            self.board.get_task("T-abc")
        with self.assertRaises(BoardError):
            self.board.get_task("T-0099")

    # ------------------------------------------------------------------ claim

    def test_claim_is_exclusive_and_stale_can_be_taken_over(self) -> None:
        self.board.add_task("boss", "작업")
        self.board.claim("agent-a", "T-0001")
        with self.assertRaises(BoardError):
            self.board.claim("agent-b", "T-0001")
        with self.assertRaises(BoardError):
            self.board.claim("agent-b", "T-0001", steal=True)  # not stale yet
        self.clock.advance(61)
        self.assertTrue(self.board.get_task("T-0001")["stale"])
        task = self.board.claim("agent-b", "T-0001", steal=True)
        self.assertEqual(task["assignee"], "agent-b")
        self.assertFalse(task["stale"])
        agents = {a["name"]: a for a in self.board.agents()}
        self.assertIsNone(agents["agent-a"]["current_task"])
        self.assertEqual(agents["agent-b"]["current_task"], "T-0001")

    def test_posting_progress_keeps_task_fresh(self) -> None:
        self.board.add_task("boss", "작업")
        self.board.claim("agent-a", "T-0001")
        self.clock.advance(50)
        self.board.post("agent-a", "T-0001", "progress", "중간 보고")
        self.clock.advance(50)
        self.assertFalse(self.board.get_task("T-0001")["stale"])

    def test_dependencies_block_claim_until_done(self) -> None:
        self.board.add_task("boss", "선행")
        self.board.add_task("boss", "후행", depends_on=["T-0001"])
        self.assertEqual(self.board.get_task("T-0002")["waiting_on"], ["T-0001"])
        with self.assertRaises(BoardError):
            self.board.claim("agent-a", "T-0002")
        self.board.claim("agent-a", "T-0001")
        self.board.set_status("agent-a", "T-0001", "done", "끝")
        self.assertEqual(self.board.get_task("T-0002")["waiting_on"], [])
        self.assertEqual(self.board.claim("agent-a", "T-0002")["status"], "in_progress")

    def test_cycle_is_rejected(self) -> None:
        self.board.add_task("boss", "A")
        self.board.add_task("boss", "B", depends_on=["T-0001"])
        with self.assertRaises(BoardError):
            self.board.edit_task("boss", "T-0001", add_deps=["T-0002"])
        with self.assertRaises(BoardError):
            self.board.edit_task("boss", "T-0001", add_deps=["T-0001"])

    # ------------------------------------------------------------------ review flow

    def test_review_required_task_needs_owner_approval(self) -> None:
        self.board.add_task("boss", "계약 v0", review_required=True)
        self.board.claim("agent-a", "T-0001")
        with self.assertRaises(BoardError):
            self.board.set_status("agent-a", "T-0001", "done")
        with self.assertRaises(BoardError):
            self.board.set_status("agent-a", "T-0001", "review")
        with self.assertRaises(BoardError):
            self.board.request_review("agent-b", "T-0001", "남의 과업")
        self.board.request_review("agent-a", "T-0001", "검토 부탁")
        self.assertEqual(self.board.get_task("T-0001")["status"], "review")
        with self.assertRaises(BoardError):
            self.board.claim("agent-b", "T-0001")
        with self.assertRaises(BoardError):
            self.board.review("agent-a", "T-0001", "approve")
        with self.assertRaises(BoardError):
            self.board.review("boss", "T-0001", "changes", "")  # comment required
        task = self.board.review("boss", "T-0001", "changes", "이름 규칙을 고쳐 주세요")
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["assignee"], "agent-a")
        self.board.request_review("agent-a", "T-0001", "반영했습니다")
        task = self.board.review("boss", "T-0001", "approve", "좋습니다")
        self.assertEqual(task["status"], "done")
        kinds = [p["kind"] for p in self.board.get_task("T-0001")["posts"]]
        self.assertEqual(kinds, ["status", "review_request", "review_result", "review_request", "review_result"])

    def test_owner_only_operations(self) -> None:
        self.board.add_task("agent-a", "작업", review_required=True)
        with self.assertRaises(BoardError):
            self.board.set_status("agent-a", "T-0001", "cancelled")
        with self.assertRaises(BoardError):
            self.board.edit_task("agent-a", "T-0001", review_required=False)
        self.board.edit_task("boss", "T-0001", review_required=False)
        self.assertFalse(self.board.get_task("T-0001")["review_required"])
        self.board.set_status("boss", "T-0001", "cancelled", "필요 없음")
        with self.assertRaises(BoardError):
            self.board.set_status("agent-a", "T-0001", "todo")
        with self.assertRaises(BoardError):
            self.board.edit_task("agent-z", "T-0001", title="남의 과업")

    def test_blocked_needs_reason_and_status_todo_releases(self) -> None:
        self.board.add_task("boss", "작업")
        self.board.claim("agent-a", "T-0001")
        with self.assertRaises(BoardError):
            self.board.set_status("agent-a", "T-0001", "blocked")
        task = self.board.set_status("agent-a", "T-0001", "blocked", "API 키 필요")
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(self.board.get_task("T-0001")["posts"][-1]["kind"], "blocker")
        task = self.board.set_status("agent-a", "T-0001", "todo", "다른 에이전트에게")
        self.assertEqual(task["status"], "todo")
        self.assertIsNone(task["assignee"])

    def test_non_assignee_cannot_change_status(self) -> None:
        self.board.add_task("boss", "작업")
        with self.assertRaises(BoardError):
            self.board.set_status("agent-a", "T-0001", "in_progress")
        self.board.claim("agent-a", "T-0001")
        with self.assertRaises(BoardError):
            self.board.set_status("agent-b", "T-0001", "done")

    # ------------------------------------------------------------------ posts & next

    def test_post_rules(self) -> None:
        self.board.add_task("boss", "작업")
        with self.assertRaises(BoardError):
            self.board.post("agent-a", "T-0001", "status", "시스템 전용 종류")
        with self.assertRaises(BoardError):
            self.board.post("agent-a", "T-0001", "progress", "   ")
        result = self.board.post("agent-a", None, "note", "게시판 전체 공지")
        self.assertIsNone(result["task"])
        self.assertEqual(self.board.posts(limit=5)[-1]["body"], "게시판 전체 공지")

    def test_next_task_prefers_priority_and_skips_blocked_deps(self) -> None:
        self.board.add_task("boss", "낮음", priority="low")
        self.board.add_task("boss", "선행", priority="medium")
        self.board.add_task("boss", "높지만 대기", priority="high", depends_on=["T-0002"])
        self.board.add_task("boss", "높음", priority="high")
        result = self.board.next_task("agent-a")
        self.assertEqual(result["next"]["key"], "T-0004")
        self.board.claim("agent-a", "T-0004")
        result = self.board.next_task("agent-a")
        self.assertEqual(result["next"]["key"], "T-0002")
        self.assertEqual([t["key"] for t in result["mine"]], ["T-0004"])

    # ------------------------------------------------------------------ seed & export

    def test_seed_file_imports_once(self) -> None:
        data = json.loads(SEED.read_text(encoding="utf-8"))
        first = self.board.seed("boss", data)
        self.assertEqual(len(first["created"]), len(data["tasks"]))
        second = self.board.seed("boss", data)
        self.assertEqual(second["created"], [])
        self.assertEqual(len(second["skipped"]), len(data["tasks"]))
        summary = self.board.summary()
        self.assertEqual([t["ext_key"] for t in summary["review_queue"]], ["P0-00"])
        p004 = next(t for t in self.board.list_tasks() if t["ext_key"] == "P0-04")
        self.assertEqual(sorted(p004["waiting_on"]), ["T-0002", "T-0004"])
        # The seeded review can be approved by the owner.
        approved = self.board.review("boss", summary["review_queue"][0]["key"], "approve")
        self.assertEqual(approved["status"], "done")

    def test_seed_validation(self) -> None:
        with self.assertRaises(BoardError) as ctx:
            self.board.seed("boss", {"tasks": [
                {"key": "A", "title": "a", "depends_on": ["B"]},
                {"key": "B", "title": "b"},
            ]})
        self.assertIn("앞쪽", str(ctx.exception))
        self.assertEqual(self.board.list_tasks(include_closed=True), [])  # nothing half-imported
        with self.assertRaises(BoardError):
            self.board.seed("boss", {"tasks": [{"key": "A", "title": "a", "status": "review"}]})
        with self.assertRaises(BoardError):
            self.board.seed("boss", {"tasks": []})

    def test_export_markdown(self) -> None:
        self.board.add_task("boss", "파이프 | 포함 제목", phase="P0")
        text = self.board.export_markdown()
        self.assertIn("파이프 \\| 포함 제목", text)
        self.assertIn("## 할 일 (1)", text)


class DefaultPathTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self._env = os.environ.pop("BOARD_DB", None)

    def tearDown(self) -> None:
        if self._env is not None:
            os.environ["BOARD_DB"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args: str, cwd: Path) -> None:
        subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)

    @unittest.skipIf(shutil.which("git") is None, "git not installed")
    def test_worktrees_share_one_board(self) -> None:
        repo = self.tmp / "repo"
        repo.mkdir()
        self._git("init", "-q", cwd=repo)
        self._git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "init", cwd=repo)
        (repo / "sub").mkdir()
        self._git("worktree", "add", "-q", str(self.tmp / "wt"), "-b", "feature", cwd=repo)
        expected = repo / ".board" / "board.db"
        self.assertEqual(find_default_db(repo), expected)
        self.assertEqual(find_default_db(repo / "sub"), expected)
        self.assertEqual(find_default_db(self.tmp / "wt"), expected)

    def test_env_and_plain_directory(self) -> None:
        plain = self.tmp / "plain"
        (plain / "deep").mkdir(parents=True)
        (plain / ".board").mkdir()
        if shutil.which("git") is None or subprocess.run(
            ["git", "rev-parse"], cwd=str(plain), capture_output=True
        ).returncode != 0:
            self.assertEqual(find_default_db(plain / "deep"), plain / ".board" / "board.db")
        os.environ["BOARD_DB"] = str(self.tmp / "x.db")
        try:
            self.assertEqual(find_default_db(plain), (self.tmp / "x.db").resolve())
        finally:
            del os.environ["BOARD_DB"]


if __name__ == "__main__":
    unittest.main()
