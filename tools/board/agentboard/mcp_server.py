"""MCP server (stdio) so agents such as Claude Code can use the board as tools.

Requires the MCP Python SDK: `pip install mcp` (1.x and 2.x are both supported).
Approving or rejecting reviews is deliberately NOT exposed here: that is the owner's job,
done from the web UI or the CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from .core import Board, BoardError

INSTRUCTIONS = (
    "Project board shared by AI agents and a human owner. Always pass your own agent name. "
    "Flow: next_task -> claim_task -> post_update (progress, at least every 30 minutes) -> "
    "request_review (for review_required tasks) or set_status done. "
    "Never try to approve reviews; only the owner can."
)


def _load_sdk() -> tuple[Any, type[Exception]]:
    """Return (server class, ToolError class) for the installed MCP SDK, or (None, ValueError)."""
    try:
        from mcp.server.mcpserver import MCPServer  # mcp >= 2
        from mcp.server.mcpserver.exceptions import ToolError

        return MCPServer, ToolError
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x
        from mcp.server.fastmcp.exceptions import ToolError as LegacyToolError

        return FastMCP, LegacyToolError
    except ImportError:
        return None, ValueError


def build_server(db_path: Path) -> Any:
    server_cls, tool_error = _load_sdk()
    if server_cls is None:
        raise BoardError("MCP 서버를 쓰려면 MCP Python SDK가 필요합니다: pip install mcp")
    db_path = Path(db_path)
    with Board(db_path):
        pass  # fail early with a clear message if the board does not exist

    app = server_cls("agent-board", instructions=INSTRUCTIONS)

    def call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
        try:
            with Board(db_path) as board:
                return getattr(board, fn_name)(*args, **kwargs)
        except BoardError as exc:
            # An anticipated rule violation: the SDK passes this message on to the agent.
            raise tool_error(str(exc)) from None

    @app.tool()
    def board_summary() -> dict[str, Any]:
        """Board overview: counts, review queue, blocked and stale tasks, active agents, recent posts."""
        return call("summary")

    @app.tool()
    def list_tasks(status: str = "", phase: str = "", include_closed: bool = False) -> list[dict[str, Any]]:
        """List tasks. status: comma-separated (backlog,todo,in_progress,blocked,review,done,cancelled)."""
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        return call("list_tasks", statuses=statuses or None, phase=phase or None, include_closed=include_closed)

    @app.tool()
    def get_task(task: str) -> dict[str, Any]:
        """Full task with dependencies and its post history. task: e.g. T-0003."""
        return call("get_task", task)

    @app.tool()
    def next_task(agent: str, phase: str = "") -> dict[str, Any]:
        """The best unclaimed task whose prerequisites are done, plus the tasks this agent already holds."""
        return call("next_task", agent, phase=phase or None)

    @app.tool()
    def claim_task(agent: str, task: str, steal: bool = False) -> dict[str, Any]:
        """Take a task (status becomes in_progress). steal=True only for a stale task of another agent."""
        return call("claim", agent, task, steal=steal)

    @app.tool()
    def release_task(agent: str, task: str, note: str = "") -> dict[str, Any]:
        """Give a task back to the queue with a reason."""
        return call("release", agent, task, note)

    @app.tool()
    def set_status(agent: str, task: str, status: str, note: str = "") -> dict[str, Any]:
        """Change status of your task: in_progress, blocked (note required), todo, or done.
        Tasks that need review cannot be set to done; use request_review."""
        return call("set_status", agent, task, status, note)

    @app.tool()
    def post_update(agent: str, body: str, kind: str = "progress", task: str = "") -> dict[str, Any]:
        """Leave a post. kind: progress, decision, question, blocker, handoff, note. task empty = board-wide."""
        return call("post", agent, task or None, kind, body)

    @app.tool()
    def request_review(agent: str, task: str, summary: str) -> dict[str, Any]:
        """Ask the owner to review your task. summary: what changed, what to check, how it was verified."""
        return call("request_review", agent, task, summary)

    @app.tool()
    def add_task(
        agent: str,
        title: str,
        body: str = "",
        phase: str = "",
        priority: str = "medium",
        review_required: bool = False,
        depends_on: Optional[list[str]] = None,
        labels: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Create a task. priority: high, medium, low. depends_on: task keys such as T-0002."""
        return call(
            "add_task",
            agent,
            title,
            body=body,
            phase=phase,
            priority=priority,
            review_required=review_required,
            depends_on=depends_on or [],
            labels=labels or [],
        )

    @app.tool()
    def heartbeat(agent: str, task: str = "") -> dict[str, Any]:
        """Signal that you are still working (keeps your task from being marked stale)."""
        return call("heartbeat", agent, task or None)

    return app


def run(db_path: Path) -> int:
    try:
        app = build_server(db_path)
    except BoardError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    app.run()
    return 0
