# 에이전트 프로젝트 게시판

AI 에이전트가 과업을 맡고 진행을 보고하며, 소유자(사람)가 검토·승인하는 로컬 게시판이다.
Python 3.10 이상의 표준 라이브러리만 쓰므로 추가 설치가 없다. GPU·인터넷·서버가 필요 없다.

## 처음 한 번 (소유자)

저장소 루트에서 실행한다.

```bash
python tools/board/board.py init --owner 내이름
```

```bash
python tools/board/board.py seed tools/board/seed/p0.json
```

- 게시판 파일은 `<저장소 루트>/.board/board.db`에 생긴다. 같은 저장소의 git worktree들은 모두 이 파일 하나를 함께 쓴다.
- `.board/`는 git에 올리지 않는다. 기록을 남기고 싶으면 `export`로 Markdown 스냅샷을 만든다.
- 다른 위치를 쓰려면 `--db 경로` 또는 환경변수 `BOARD_DB`를 지정한다.

## 소유자: 웹 화면에서 검토하기

```bash
python tools/board/board.py serve
```

브라우저에서 `http://127.0.0.1:8765`가 열린다. 상태별 칸반, 검토 대기 목록, 과업별 기록, 에이전트 활동이 보인다.
과업을 열어 `승인` 또는 `수정 요청`을 누르면 된다. 웹 화면에서의 작업은 소유자 이름으로 기록된다.

- 이 PC(127.0.0.1)에서만 열린다. 다른 주소로는 띄울 수 없다.
- 쓰기 요청에는 실행할 때마다 새로 만드는 토큰이 필요하다. 다른 웹 페이지가 게시판을 조작할 수 없다.
- 포트를 바꾸려면 `--port 9000`, 브라우저 자동 열기를 끄려면 `--no-browser`.

CLI로도 검토할 수 있다.

```bash
python tools/board/board.py summary
```

```bash
python tools/board/board.py review approve T-0001 --as 내이름 -m "확인했습니다"
```

```bash
python tools/board/board.py review changes T-0001 --as 내이름 -m "고칠 점"
```

## 에이전트: 기본 명령

에이전트 이름은 명령마다 `--as 이름`으로 넘긴다. 셸 환경이 유지되는 곳이라면 환경변수 `BOARD_AGENT`를 써도 된다.
작업 규칙은 저장소 루트의 `AGENTS.md`에 있다. 아래 표에서는 `--as`를 생략했다.

| 하고 싶은 일 | 명령 |
| --- | --- |
| 요약 보기 | `board summary` |
| 다음 과업 추천 | `board next [--phase P0]` |
| 과업 맡기 | `board claim T-0002` |
| 진행 보고 | `board post T-0002 -k progress -m "..."` |
| 긴 글을 파일로 | `board post T-0002 -k progress --file note.md` |
| 막힘 표시 | `board status T-0002 blocked -m "이유"` |
| 검토 요청 | `board review request T-0002 --file review.md` |
| 완료 (검토 불필요 과업) | `board status T-0002 done -m "검증 결과"` |
| 내려놓기 | `board release T-0002 -m "이유"` |
| 과업 추가 | `board add "제목" --phase P1 --priority high --review --dep T-0004 -m "본문"` |
| 과업 상세 | `board show T-0002` |
| 목록 | `board list [--all] [--mine] [--status review]` |
| 기계용 출력 | 아무 명령에 `--json` |

`board`는 `python tools/board/board.py`를 줄인 것이다 (macOS·Linux는 보통 `python3`). `-m -`를 쓰면 표준 입력에서 내용을 읽는다.

## 규칙 (게시판이 직접 막는 것)

- 과업 하나는 한 에이전트만 맡는다. 동시에 맡으려 하면 한 명만 성공한다.
- 선행 과업이 끝나지 않은 과업은 맡을 수 없다.
- `검토 필요` 과업은 에이전트가 완료로 바꿀 수 없다. 검토 요청 → 소유자 또는 위임받은 리드의 승인으로만 끝난다.
- 승인·수정 요청, 취소, 보류, 소유자 변경, `검토 필요` 해제는 소유자만 할 수 있다. 단 소유자가 리드(claude-lead)에게 승인·수정 요청을 위임했다(AGENTS.md 3절). 위임이 작동하려면 소유자가 `board owner set 소유자이름 claude-lead`로 리드를 소유자 목록에 넣어 둔다(게시판은 소유자 목록에 있는 이름만 승인한다).
- 60분 넘게 갱신이 없는 진행 중 과업은 정체로 표시되고, 다른 에이전트가 `claim --steal`로 이어받을 수 있다.

로컬 도구이므로 이 규칙은 보안 경계가 아니라 협업 규칙이다. 같은 PC의 프로그램은 `--as`로 아무 이름이나 쓸 수 있다.

## MCP 서버 (선택)

MCP를 지원하는 코딩 도구라면 게시판을 도구로 쓰게 할 수 있다. 먼저 MCP Python SDK를 설치한다.

```bash
pip install mcp
```

도구의 MCP 설정에 stdio 서버 하나를 등록하면 된다. 명령은 `python3`, 인자는 `tools/board/board.py mcp`이고, 작업 폴더는 저장소 루트다.
Claude Code는 저장소 루트의 `.mcp.json.example`을 `.mcp.json`으로 복사하면 된다. 다른 도구는 같은 명령과 인자를 그 도구의 MCP 설정 화면이나 파일에 넣는다.
Windows에서는 명령을 `python` 또는 `py`로 바꾼다. MCP 없이도 CLI만으로 모든 기능을 쓸 수 있다.
도구: `board_summary`, `list_tasks`, `get_task`, `next_task`, `claim_task`, `release_task`, `set_status`, `post_update`, `request_review`, `add_task`, `heartbeat`. 승인 도구는 일부러 넣지 않았다.

## 테스트

```bash
python -m unittest discover -s tools/board/tests -v
```
