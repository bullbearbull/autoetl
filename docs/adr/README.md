# 설계 결정 기록(ADR)

핵심 설계 결정을 맥락·결정·대안·결과로 기록한다. 원천은 소유자가 관리하는 계획 문서
(`docs/PLAN.md`)이고, ADR은 그 결정의 이유와 결과를 남긴 사본이다. 결정을 바꿀 때는
새 ADR을 쓰거나 기존 ADR의 상태를 바꾼다.

| 번호 | 결정 | 상태 |
| --- | --- | --- |
| [0001](0001-determinism-first.md) | 결정론 우선 — 계산은 코드, LLM은 정해진 결정 지점만 | 수락됨 |
| [0002](0002-local-first.md) | 로컬 설치형 우선 — 한 프로세스, Redis·Docker 없음 | 수락됨 |
| [0003](0003-library-first.md) | 라이브러리 우선 구조 — 엔진은 순수 패키지, 서버·UI는 얇은 계층 | 수락됨 |
| [0004](0004-postgresql-first.md) | PostgreSQL 우선 — 다른 DB는 sqlglot 방언 변환으로 확장 | 수락됨 |
| [0005](0005-tech-stack.md) | 기술 스택 — Python 3.10+·uv, 표 허용형 의존성 | 수락됨 |
| [0006](0006-frontend-in-wheel.md) | 프런트엔드 — Vite SPA 빌드 결과물을 휠에 포함 | 수락됨 |
