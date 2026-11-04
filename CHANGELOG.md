# Changelog

이 문서는 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고
[Semantic Versioning](https://semver.org/lang/ko/)을 사용한다.

## [Unreleased]

### Added

- SOP v1 원본(`sop/v1.yaml`, S0~S14 단계·점검 항목·실패 시 행동·LLM 프롬프트)과 생성기(`tools/build_sop.py` → `docs/sop-v1.md`, `docs/prompts-v1.md`) (T-0006)
- 단계별 계약 v0: S0~S14 산출물 15개의 Pydantic 모델과 JSON Schema 내보내기(`tools/export_contracts.py` → `schemas/`), 계약 버전 규칙 (T-0005)
- 저장소 골격: uv 기반 `pyproject.toml`, 순수 Python 휠 구성, `src/autoetl` 패키지와 `py.typed` (T-0002)
- 품질 도구: ruff(lint·format), mypy(strict), pytest 설정과 `uv.lock` (T-0002)
- CI: Windows·macOS·Linux × Python 3.10~3.13 테스트 매트릭스, 린트·타입 검사, 의존성 라이선스 자동 검사(`tools/license_check.py`) (T-0002)
- 오픈소스 기본 파일: LICENSE(Apache-2.0), README(한·영), 기여 안내, 행동 강령, 보안 정책, 이슈·PR 템플릿 (T-0003)
