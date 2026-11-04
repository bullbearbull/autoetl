import http.client
import json
import re
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentboard.core import Board  # noqa: E402
from agentboard.web import make_server  # noqa: E402


class WebTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "board.db"
        with Board.init(self.db, owner="boss") as board:
            board.add_task("agent-a", "<script>alert(1)</script> 제목", review_required=True)
        self.server = make_server(self.db, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, method: str, path: str, body=None, *, host=None, token=None, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = ctype
        if token:
            headers["X-Board-Token"] = token
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res, raw

    def test_index_has_token_nonce_and_csp(self) -> None:
        res, raw = self.request("GET", "/")
        self.assertEqual(res.status, 200)
        html = raw.decode("utf-8")
        self.assertNotIn("__NONCE__", html)
        self.assertNotIn("__CONFIG__", html)
        self.assertIn(self.server.token, html)
        csp = res.getheader("Content-Security-Policy")
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        self.assertIn(f'nonce="{nonce}"', html)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_foreign_host_is_rejected(self) -> None:
        res, _ = self.request("GET", "/api/summary", host="evil.example:80")
        self.assertEqual(res.status, 403)
        res, _ = self.request("POST", "/api/tasks", {"title": "x"}, host="evil.example", token=self.server.token)
        self.assertEqual(res.status, 403)

    def test_writes_need_token_and_json(self) -> None:
        res, _ = self.request("POST", "/api/tasks", {"title": "x"})
        self.assertEqual(res.status, 403)
        res, _ = self.request("POST", "/api/tasks", {"title": "x"}, token="wrong")
        self.assertEqual(res.status, 403)
        res, _ = self.request("POST", "/api/tasks", {"title": "x"}, token=self.server.token, ctype="text/plain")
        self.assertEqual(res.status, 415)
        res, _ = self.request("OPTIONS", "/api/tasks")
        self.assertEqual(res.status, 405)

    def test_owner_flow_through_api(self) -> None:
        res, raw = self.request("POST", "/api/tasks", {"title": "웹에서 만든 과업", "phase": "P1", "review_required": False},
                                token=self.server.token)
        self.assertEqual(res.status, 200, raw)
        created = json.loads(raw)["data"]
        self.assertEqual(created["created_by"], "boss")

        with Board(self.db) as board:
            board.claim("agent-a", "T-0001")
            board.request_review("agent-a", "T-0001", "검토 부탁")
        res, raw = self.request("POST", "/api/tasks/T-0001/posts", {"kind": "question", "body": "테스트는?"},
                                token=self.server.token)
        self.assertEqual(res.status, 200, raw)
        res, raw = self.request("POST", "/api/tasks/T-0001/review", {"decision": "changes", "comment": ""},
                                token=self.server.token)
        self.assertEqual(res.status, 400)
        self.assertIn("검토 의견", json.loads(raw)["error"])
        res, raw = self.request("POST", "/api/tasks/T-0001/review", {"decision": "approve", "comment": "OK"},
                                token=self.server.token)
        self.assertEqual(res.status, 200, raw)
        self.assertEqual(json.loads(raw)["data"]["status"], "done")

        res, raw = self.request("GET", "/api/tasks/T-0001")
        task = json.loads(raw)["data"]
        self.assertEqual(task["title"], "<script>alert(1)</script> 제목")  # stored verbatim, rendered as text
        self.assertEqual([p["kind"] for p in task["posts"]], ["status", "review_request", "question", "review_result"])

        res, raw = self.request("GET", "/api/tasks?closed=1")
        self.assertEqual(len(json.loads(raw)["data"]), 2)
        res, raw = self.request("GET", "/api/tasks")
        self.assertEqual([t["key"] for t in json.loads(raw)["data"]], ["T-0002"])

    def test_errors_are_json(self) -> None:
        res, raw = self.request("GET", "/api/tasks/T-0099")
        self.assertEqual(res.status, 400)
        self.assertIn("없는 과업", json.loads(raw)["error"])
        res, _ = self.request("GET", "/nope")
        self.assertEqual(res.status, 404)
        res, _ = self.request("POST", "/api/tasks", None, token=self.server.token)
        self.assertEqual(res.status, 415)


if __name__ == "__main__":
    unittest.main()
