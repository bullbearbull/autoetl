# Changelog

이 문서는 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고
[Semantic Versioning](https://semver.org/lang/ko/)을 사용한다.

## [Unreleased]

### Added

- 벤치 데이터셋 생성기 v0(`bench/generator/`): 합성 관계형 팩 2종, 시드 고정 생성, 리프→루트 조인 평면화, 잡음 변형 7종(결측·형식 혼재·앞자리 0·다중 헤더·CP949·hard), 정답 스키마·메니페스트 출력 (T-0008)
- 데이터·로그 등급 정책 v0: 문서(`docs/policy/data-log-levels.md`)와 기계 판독형 정의(`src/autoetl/security/levels.py`), 카나리 테스트 설계, 정합성 테스트 (T-0007)
- SOP v1 원본(`sop/v1.yaml`, S0~S14 단계·점검 항목·실패 시 행동·LLM 프롬프트)과 생성기(`tools/build_sop.py` → `docs/sop-v1.md`, `docs/prompts-v1.md`) (T-0006)
- 단계별 계약 v0: S0~S14 산출물 15개의 Pydantic 모델과 JSON Schema 내보내기(`tools/export_contracts.py` → `schemas/`), 계약 버전 규칙 (T-0005)
- 저장소 골격: uv 기반 `pyproject.toml`, 순수 Python 휠 구성, `src/autoetl` 패키지와 `py.typed` (T-0002)
- 품질 도구: ruff(lint·format), mypy(strict), pytest 설정과 `uv.lock` (T-0002)
- CI: Windows·macOS·Linux × Python 3.10~3.13 테스트 매트릭스, 린트·타입 검사, 의존성 라이선스 자동 검사(`tools/license_check.py`) (T-0002)
- 오픈소스 기본 파일: LICENSE(Apache-2.0), README(한·영), 기여 안내, 행동 강령, 보안 정책, 이슈·PR 템플릿 (T-0003)
