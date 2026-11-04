import asyncio
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BOARD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOARD_DIR))

from agentboard.core import Board  # noqa: E402

HAS_MCP = importlib.util.find_spec("mcp") is not None


def _is_error(result) -> bool:
    return bool(getattr(result, "isError", None) or getattr(result, "is_error", None))


def _payload(result):
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if structured is not None:
        return structured.get("result", structured) if isinstance(structured, dict) else structured
    text = "".join(getattr(c, "text", "") for c in result.content)
    return json.loads(text)


@unittest.skipUnless(HAS_MCP, "MCP Python SDK not installed (pip install mcp)")
class McpTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.db = str(Path(self.tmp) / "board.db")
        with Board.init(self.db, owner="boss") as board:
            board.add_task("boss", "MCP 과업", review_required=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_agent_flow_over_stdio(self) -> None:
        asyncio.run(self._flow())
        with Board(self.db) as board:
            task = board.get_task("T-0001")
        self.assertEqual(task["status"], "review")
        self.assertEqual([p["kind"] for p in task["posts"]], ["status", "progress", "review_request"])

    async def _flow(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=[str(BOARD_DIR / "board.py"), "--db", self.db, "mcp"],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                self.assertIn("claim_task", tools)
                self.assertIn("request_review", tools)
                self.assertFalse(any("approve" in name for name in tools))

                result = await session.call_tool("next_task", {"agent": "claude-a"})
                self.assertFalse(_is_error(result))
                self.assertEqual(_payload(result)["next"]["key"], "T-0001")

                result = await session.call_tool("claim_task", {"agent": "claude-a", "task": "T-0001"})
                self.assertFalse(_is_error(result))

                result = await session.call_tool(
                    "post_update", {"agent": "claude-a", "task": "T-0001", "body": "한 일: 테스트"}
                )
                self.assertFalse(_is_error(result))

                result = await session.call_tool(
                    "set_status", {"agent": "claude-a", "task": "T-0001", "status": "done"}
                )
                self.assertTrue(_is_error(result))
                message = "".join(getattr(c, "text", "") for c in result.content)
                self.assertIn("검토", message)

                result = await session.call_tool(
                    "request_review", {"agent": "claude-a", "task": "T-0001", "summary": "검토 부탁"}
                )
                self.assertFalse(_is_error(result))


if __name__ == "__main__":
    unittest.main()
