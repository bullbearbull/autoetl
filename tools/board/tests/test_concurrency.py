import multiprocessing
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BOARD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOARD_DIR))

from agentboard.core import Board, BoardError  # noqa: E402


def _try_claim(args: tuple[str, str, str]) -> bool:
    board_dir, db, agent = args
    if board_dir not in sys.path:
        sys.path.insert(0, board_dir)
    from agentboard.core import Board as _Board, BoardError as _BoardError

    try:
        with _Board(db) as board:
            board.claim(agent, "T-0001")
        return True
    except _BoardError:
        return False


class ConcurrencyTestCase(unittest.TestCase):
    def test_only_one_process_wins_a_claim(self) -> None:
        tmp = tempfile.mkdtemp()
        try:
            db = str(Path(tmp) / "board.db")
            with Board.init(db, owner="boss") as board:
                board.add_task("boss", "경쟁 과업")
            ctx = multiprocessing.get_context("spawn")
            with ctx.Pool(8) as pool:
                results = pool.map(_try_claim, [(str(BOARD_DIR), db, f"agent-{i}") for i in range(8)])
            self.assertEqual(sum(results), 1)
            with Board(db) as board:
                task = board.get_task("T-0001")
                self.assertEqual(task["status"], "in_progress")
                self.assertEqual(len([p for p in task["posts"] if p["kind"] == "status"]), 1)
                with self.assertRaises(BoardError):
                    board.claim("late-agent", "T-0001")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
