# ADR-0005: 기술 스택 — Python 3.10+, uv, 허용형 라이선스 의존성만

- 상태: 수락됨 (2026-11-04, 계획 확정 사항 기반)
- 결정 주체: 소유자

## 맥락

로컬 설치형 오픈소스로서 Windows·macOS·Linux, x86_64·arm64, Python 3.10 이상을 모두
지원해야 한다. 설치 실패와 리소스 낭비를 최소화하고, GPU가 없어도 동작해야 한다.
오픈소스(Apache-2.0 권장)로 공개하므로 핵심 패키지에 AGPL·GPL 의존성이 들어오면 안 된다.

## 결정

계획의 기술 스택 표를 그대로 따른다.

| 영역 | 선택 |
| --- | --- |
| 런타임 | Python 3.10 이상, uv |
| 분석 | pandas, numpy, scipy, scikit-learn, statsmodels. 대용량은 DuckDB + pyarrow |
| 파일 입력 | charset-normalizer, python-calamine/openpyxl, pyarrow, lxml |
| SQL | SQLAlchemy 2.x, psycopg 3, sqlglot |
| 계약 | Pydantic v2 (JSON Schema로 출력 강제에도 사용) |
| LLM 연동 | openai·anthropic 공식 SDK의 base_url 재지정을 감싼 자체 경량 어댑터 |
| 로컬 서버 | FastAPI + Uvicorn (127.0.0.1 전용), SSE |
| 작업 실행 | 프로세스 내 실행기 + SQLite 작업 테이블 (Redis는 서버형에서만) |
| 메타데이터 | SQLite(WAL) + Alembic |
| 프런트엔드 | React + TypeScript + Vite SPA (ADR-0006 참조) |
| 사양 점검 | psutil, 선택적으로 NVML·Apple Silicon 정보 |
| 리포트 | Jinja2 → HTML/PDF/Markdown |
| 로깅 | structlog + 민감정보 가림 처리기 |

운영 규칙:

- 새 의존성은 허용형 라이선스(MIT·BSD·Apache-2.0·PSF·MPL-2.0 계열)만 쓰고, CI가
  `tools/license_check.py`로 자동 검사한다. 새 의존성 추가는 소유자 검토 대상이다.
- 선택 기능의 무거운 의존성은 쓸 때만 import한다(설치 선택 그룹 `[ui]`, `[local]`, `[ocr]`,
  `[server]`).
- 순수 Python 휠로 배포해 3개 OS·CPU 아키텍처에서 같은 바이너리 호환성을 유지한다.
- 게시판(`tools/board`)처럼 저장소 도구는 표준 라이브러리만으로 만들어 설치 요구를
  만들지 않는다.

## 대안

- **대용량 엔진 기본 탑재(Spark, Dask)**: 로컬 설치형 전제에서 설치·자원 비용이 과하다.
  DuckDB 스트리밍으로 전수 통계 요구를 대부분 충족할 수 있다.
- **ORM 없이 raw SQL 직접 실행**: 매핑·방언 관리 비용이 코드 전반으로 퍼진다.
- **poetry·piprequirements 관리**: uv 대비 잠금 속도·워크플로 단순성이 떨어진다(이미
  저장소는 uv로 확정·구성됨).

## 결과

- 긍정: 넓은 호환성과 가벼운 설치, 라이선스 정책 자동 검사, 재현 가능한 잠금(uv.lock).
- 부정: pandas 계열 의존성 세트의 용량은 피할 수 없다(지연 import·선택 그룹으로 완화).
  일부 최신 전용 기능(예: 최신 문법)을 포기하고 3.10 호환을 유지해야 한다.
