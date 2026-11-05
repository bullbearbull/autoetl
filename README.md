# Auto ETL

측정은 코드가, 판단은 LLM이 맡는 로컬 설치형 ETL·스키마 설계 도구. CSV·엑셀을 분석해
PostgreSQL 스키마 후보와 적재 플랜을 자동으로 만든다. 어떤 모델을 써도 같은 품질이 나오도록
흐름 전체를 코드가 제어한다.

> 상태: 초기 개발 중. 아직 PyPI에 공개하지 않았다.
> [English README](README.en.md)

## 무엇을 만드나

CSV·엑셀 파일을 넣으면:

1. 결정론 분석 엔진이 구조 프로파일, 타입 추론, 관계 분석, 품질 진단, 민감도 분류를 코드로 계산한다.
2. LLM 하네스가 정해진 결정 지점에서만 선택·수정·설명을 돕는다. LLM 출력은 계약 스키마로 강제한다.
3. 3NF·스타 스키마 등 후보 DDL, 점수표, 적재 플랜을 내놓는다.

핵심 원칙:

| 원칙 | 내용 |
| --- | --- |
| 결정론 우선 | 통계·타입·관계·후보 스키마·DDL은 코드가 계산한다. LLM은 정해진 지점에서 선택과 설명만 |
| 계약 우선 | 단계별 입출력을 Pydantic 스키마로 고정해 모델 교체의 영향을 차단한다 |
| 로컬 우선 | 한 프로세스에 엔진·로컬 서버·SQLite 메타데이터를 담는다. Redis·Docker·GPU 불필요가 목표 |
| 보안 기본값 | Secure 모드에서 외부 LLM으로 나가는 실제 셀 값은 0건 |

## 개발

Python 3.10 이상, [uv](https://docs.astral.sh/uv/)로 환경을 관리한다.

```bash
uv sync                 # 의존성 설치(.venv 생성)
uv run pytest           # 테스트
uv run ruff check .     # 린트
uv run ruff format --check .
uv run mypy             # 타입 검사(strict)
```

CI는 Windows·Linux × Python 3.10~3.13에서 같은 검사와 의존성 라이선스 검사를 돌린다.
(macOS 검사는 비용 문제로 보류 중 — 2026-11-06 소유자 결정. 코드의 macOS 지원은 그대로다.)
기여 방법은 [CONTRIBUTING.md](CONTRIBUTING.md), AI 코딩 도구의 작업 규칙은 [AGENTS.md](AGENTS.md)에 있다.

## 라이선스

[Apache-2.0](LICENSE)
