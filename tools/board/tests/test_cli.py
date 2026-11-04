import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BOARD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOARD_DIR))

from agentboard.cli import main  # noqa: E402


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.db = str(Path(self.tmp) / ".board" / "board.db")
        self._env = {k: os.environ.pop(k, None) for k in ("BOARD_AGENT", "BOARD_DB", "BOARD_OWNER")}
        code, out, _ = self.run_cli("init", "--owner", "boss")
        self.assertEqual(code, 0, out)

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin", io.StringIO(stdin)):
            code = main(["--db", self.db, *args])
        return code, out.getvalue(), err.getvalue()

    def test_init_creates_gitignore(self) -> None:
        ignore = Path(self.db).parent / ".gitignore"
        self.assertTrue(ignore.exists())
        self.assertIn("*", ignore.read_text(encoding="utf-8"))

    def test_full_agent_flow(self) -> None:
        code, out, err = self.run_cli("add", "계약 v0", "--phase", "P0", "--review", "--as", "agent-a", "-m", "본문")
        self.assertEqual(code, 0, err)
        self.assertIn("T-0001", out)
        # --as may come after the subcommand or (via env) not at all
        os.environ["BOARD_AGENT"] = "agent-a"
        code, out, err = self.run_cli("next")
        self.assertEqual(code, 0, err)
        self.assertIn("board claim T-0001", out)
        self.assertEqual(self.run_cli("claim", "T-0001")[0], 0)
        code, out, err = self.run_cli("post", "T-0001", "-k", "progress", "-m", "-", stdin="한 일: 모델 작성\n검증: ok\n")
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("status", "T-0001", "done")
        self.assertEqual(code, 2)
        self.assertIn("검토", err)
        review_file = Path(self.tmp) / "review.md"
        review_file.write_text("변경 요약: 계약 추가\n", encoding="utf-8")
        self.assertEqual(self.run_cli("review", "request", "T-0001", "--file", str(review_file))[0], 0)
        code, _, err = self.run_cli("review", "approve", "T-0001")
        self.assertEqual(code, 2)
        self.assertIn("소유자", err)
        code, out, err = self.run_cli("review", "approve", "T-0001", "--as", "boss", "-m", "좋음")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("show", "T-0001")
        self.assertIn("완료", out)
        self.assertIn("한 일: 모델 작성", out)
        self.assertIn("변경 요약: 계약 추가", out)

    def test_json_output_and_global_options(self) -> None:
        self.run_cli("add", "작업", "--as", "agent-a")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--json", "--db", self.db, "list"])
        self.assertEqual(code, 0, err.getvalue())
        data = json.loads(out.getvalue())
        self.assertEqual(data[0]["key"], "T-0001")
        code, out, _ = self.run_cli("summary", "--json")
        self.assertEqual(json.loads(out)["counts"]["todo"], 1)

    def test_write_without_actor_fails_clearly(self) -> None:
        code, _, err = self.run_cli("add", "작업")
        self.assertEqual(code, 2)
        self.assertIn("BOARD_AGENT", err)

    def test_seed_and_export(self) -> None:
        code, out, err = self.run_cli("seed", str(BOARD_DIR / "seed" / "p0.json"))
        self.assertEqual(code, 0, err)
        self.assertIn("새로 만든 과업 11개", out)
        code, out, _ = self.run_cli("seed", str(BOARD_DIR / "seed" / "p0.json"))
        self.assertIn("건너뛴 과업 11개", out)
        snapshot = Path(self.tmp) / "BOARD.md"
        self.assertEqual(self.run_cli("export", "--out", str(snapshot))[0], 0)
        text = snapshot.read_text(encoding="utf-8")
        self.assertIn("## 검토 대기 (1)", text)
        code, out, _ = self.run_cli("summary")
        self.assertIn("검토 대기: 1건", out)
        self.assertIn("claude-setup", out)

    def test_owner_set_requires_owner(self) -> None:
        code, _, err = self.run_cli("owner", "set", "mallory", "--as", "agent-a")
        self.assertEqual(code, 2)
        code, out, err = self.run_cli("owner", "set", "boss", "alice", "--as", "boss")
        self.assertEqual(code, 0, err)
        self.assertIn("boss, alice", out)

    def test_entry_script_runs_as_subprocess(self) -> None:
        env = dict(os.environ, BOARD_AGENT="agent-sub", PYTHONIOENCODING="utf-8")
        result = subprocess.run(
            [sys.executable, str(BOARD_DIR / "board.py"), "--db", self.db, "add", "서브프로세스 과업"],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("T-0001", result.stdout)
        result = subprocess.run(
            [sys.executable, str(BOARD_DIR / "board.py"), "--help"],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("claim", result.stdout)


if __name__ == "__main__":
    unittest.main()
