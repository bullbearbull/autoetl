"""Local web UI for the owner: http://127.0.0.1:8765

Security model (local tool, single user):
- Binds to a loopback address only.
- Rejects requests whose Host header is not that loopback address (blocks DNS rebinding).
- Every write needs the X-Board-Token header. The token is created per run and only
  embedded in the page this server serves, so other web pages cannot forge writes.
- All text is rendered with textContent in the browser; nothing written by agents is
  interpreted as HTML.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from .core import Board, BoardError

MAX_REQUEST_BYTES = 64 * 1024
_STATIC = Path(__file__).with_name("static")
_TASK_PATH = re.compile(r"^/api/tasks/(T-\d+|\d+)(?:/(posts|status|review))?$", re.IGNORECASE)


class BoardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.token = secrets.token_urlsafe(32)
        family_host = address[0]
        if ":" in family_host:
            import socket

            self.address_family = socket.AF_INET6
        super().__init__(address, BoardHandler)
        port = self.server_address[1]
        self.allowed_hosts = {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        }
        with Board(self.db_path) as board:
            self.owner = board.primary_owner()

    @property
    def url(self) -> str:
        host = self.server_address[0]
        host = f"[{host}]" if ":" in host else host
        return f"http://{host}:{self.server_address[1]}/"


class BoardHandler(BaseHTTPRequestHandler):
    server: BoardServer
    server_version = "AgentBoard/1"
    sys_version = ""

    # Quiet by default: request lines can contain task titles; never log bodies.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    # ------------------------------------------------------------ responses

    def _security_headers(self, nonce: Optional[str] = None) -> None:
        script = f"'nonce-{nonce}'" if nonce else "'none'"
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'none'; script-src {script}; style-src {script}; connect-src 'self'; "
            "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.allowed_hosts

    # ------------------------------------------------------------ GET

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "허용되지 않은 Host입니다.")
            return
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        try:
            if path in ("/", "/index.html"):
                self._index()
            elif path == "/api/summary":
                with Board(self.server.db_path) as board:
                    self._json(HTTPStatus.OK, {"ok": True, "data": board.summary()})
            elif path == "/api/tasks":
                statuses = [s for s in ",".join(query.get("status", [])).split(",") if s]
                with Board(self.server.db_path) as board:
                    data = board.list_tasks(
                        statuses=statuses or None,
                        phase=(query.get("phase") or [None])[0] or None,
                        include_closed=(query.get("closed") or ["0"])[0] == "1",
                    )
                self._json(HTTPStatus.OK, {"ok": True, "data": data})
            else:
                match = _TASK_PATH.match(path)
                if match and match.group(2) is None:
                    with Board(self.server.db_path) as board:
                        self._json(HTTPStatus.OK, {"ok": True, "data": board.get_task(match.group(1))})
                else:
                    self._error(HTTPStatus.NOT_FOUND, "없는 주소입니다.")
        except BoardError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:  # pragma: no cover - keep the server alive, never echo internals
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "서버 내부 오류가 발생했습니다.")

    def _index(self) -> None:
        nonce = secrets.token_urlsafe(16)
        config = json.dumps({"token": self.server.token, "owner": self.server.owner})
        config = config.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        html = (_STATIC / "index.html").read_text(encoding="utf-8")
        html = html.replace("__NONCE__", nonce).replace("__CONFIG__", config)
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers(nonce)
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------ POST

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "허용되지 않은 Host입니다.")
            return
        token = self.headers.get("X-Board-Token", "")
        if not hmac.compare_digest(token.encode("utf-8"), self.server.token.encode("utf-8")):
            self._error(HTTPStatus.FORBIDDEN, "토큰이 없거나 맞지 않습니다. 화면을 새로고침하세요.")
            return
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON 요청만 받습니다.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "요청이 너무 큽니다.")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "JSON 형식이 올바르지 않습니다.")
            return

        actor = self.server.owner
        path = urlsplit(self.path).path
        try:
            with Board(self.server.db_path) as board:
                if path == "/api/tasks":
                    data = board.add_task(
                        actor,
                        str(payload.get("title", "")),
                        body=str(payload.get("body", "")),
                        phase=str(payload.get("phase", "")),
                        priority=str(payload.get("priority", "medium")),
                        review_required=bool(payload.get("review_required", False)),
                        labels=[str(x) for x in payload.get("labels", []) or []],
                        depends_on=[str(x) for x in payload.get("depends_on", []) or []],
                        status=str(payload.get("status", "todo")),
                    )
                else:
                    match = _TASK_PATH.match(path)
                    if not match or match.group(2) is None:
                        self._error(HTTPStatus.NOT_FOUND, "없는 주소입니다.")
                        return
                    ref, action = match.group(1), match.group(2)
                    if action == "posts":
                        data = board.post(actor, ref, str(payload.get("kind", "note")), str(payload.get("body", "")))
                    elif action == "status":
                        data = board.set_status(actor, ref, str(payload.get("status", "")), str(payload.get("note", "")))
                    else:
                        data = board.review(actor, ref, str(payload.get("decision", "")), str(payload.get("comment", "")))
            self._json(HTTPStatus.OK, {"ok": True, "data": data})
        except BoardError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:  # pragma: no cover
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "서버 내부 오류가 발생했습니다.")

    def do_PUT(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "지원하지 않는 메서드입니다.")

    do_DELETE = do_PATCH = do_OPTIONS = do_PUT  # no CORS preflight is ever answered


def make_server(db_path: Path, host: str = "127.0.0.1", port: int = 8765) -> BoardServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise BoardError("웹 화면은 이 PC(127.0.0.1)에서만 열 수 있습니다.")
    bind = "127.0.0.1" if host == "localhost" else host
    return BoardServer((bind, port), Path(db_path))


def serve(db_path: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    try:
        server = make_server(db_path, host, port)
    except OSError as exc:
        raise BoardError(f"{host}:{port}에서 서버를 열 수 없습니다 ({exc}). --port로 다른 포트를 지정하세요.") from exc
    print(f"에이전트 게시판: {server.url}  (종료: Ctrl+C)")
    print(f"게시판 파일: {server.db_path}   소유자로 동작: {server.owner}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(server.url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n게시판 서버를 종료합니다.", file=sys.stderr)
    finally:
        server.server_close()
    return 0
