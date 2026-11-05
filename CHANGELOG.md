# Changelog

이 문서는 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고
[Semantic Versioning](https://semver.org/lang/ko/)을 사용한다.

## [Unreleased]

### Changed

- 벤치 v0.1: 평면 헤더에서 정답 테이블 정보를 제거했다(T-0015). 헤더는 접두사 없는 컬럼 이름이고 조인 키(PK=FK)는 부모 PK 이름 하나로 처음 나오는 위치에 한 번만 나온다(orders_kr 19컬럼, music 13컬럼). 채점 키를 헤더 문자열에서 컬럼 ID로 바꿨다 — answer.json 0.2.0(`column_id`, `variant_headers`), FK 식별은 (자식 PK·FK 컬럼 ID 집합) 쌍, 배정(ARI)에서 조인 키는 PK 소유 테이블 귀속, 타입은 컬럼 ID당 1회 집계.
- 벤치 잡음이 컬럼 이름이 아니라 팩 선언의 kind·키 여부로 대상을 고른다(T-0015). PK·조인 키에는 결측을 넣지 않고, music 팩에 5자리 코드 컬럼(albums.catalog_no)을 추가해 leading_zero 변형이 두 팩 모두에서 의미 있게 했다. 다중 헤더 그룹 행은 컬럼 성격 라벨(식별자·날짜·금액 등)로 바꾸고, hard 변형은 이름 없는 필드 헤더(col_01 형식)를 쓴다.
- 기준선 예측 3종(`bench/baselines.py`: 단일 와이드·헤더 이름 규칙·'_id' 위치 끊기)과 그 점수를 bench/README.md에 기록했다(시드 42: 와이드 21.68~23.85, 이름 규칙 21.68~31.46, 위치 끊기 21.68~53.85) — 헤더 이름 신호를 쓴 최선 예측의 수준을 판정 기준점으로 삼으며 문서↔코드 일치 테스트가 대조한다 (T-0015)
- 리드 재검토 반영(T-0015): 다중 헤더 그룹 행에서 키 컬럼을 구별하지 않는다(id·fk도 '수치' — '식별자' 위치가 테이블 경계를 새던 재현 53.68점 해소), 같은 부모 PK를 다른 이름으로 참조하는 FK는 조용히 합쳐지지 않고 생성기 오류를 낸다, hard 헤더 전용 공격이 clean 이름 신호를 넘지 못하는 테스트 추가.

### Fixed

- 채점: 같은 PK·FK·FD를 여러 번 예측해도 감점이 없던 구멍을 막았다 — 정밀도 분모를 예측 개수(중복 포함)로 하고 분자는 같은 정답을 한 번만 인정한다. 기준선(전체 모델 최고 점수)이 0 이하일 때는 최소·권장 모델 판정을 보류한다(0 ≥ 0×0.85로 무의미하게 지정되던 것 방지) (T-0016)

- 채점: 정답 FK·FD가 비어 있을 때 예측이 비어 있어야 만점이고, 스팸 FK·FD를 늘어놓으면 0점으로 바꿨다(타입 F1·PK F1·FD 복원율도 같은 규칙으로 통일). 배정 ARI에 겹침 비율(겹친 컬럼 수/정답 컬럼 수)을 곱해 컬럼 1개만 예측해도 만점이 나오던 비단조 구간을 제거 (T-0014)
- 러너: 원본 CSV 헤더에 같은 이름이 여러 개면(예: multi_header 변형) 소스 이름에 발생 접미(`#N`)를 붙이고 대상 이름을 위치 기반으로 만들어 유일화. 계약 산출물 전부(IngestManifest·LoadPlan 등)가 유일한 소스 이름을 참조한다 (T-0014)
- CLI: `--out`이 입력 파일의 하드 링크·대소문자 무시 파일시스템 표기일 때도 덮어쓰기를 거부(`samefile` 비교). 계약 위반 안내 문구가 실제 데이터를 공개 이슈에 첨부하도록 유도하지 않게 수정(명령·버전·컬럼 수·헤더 형태만 요청) (T-0014)

### Added

- 빈 파이프라인 러너 v0(`src/autoetl/core/pipeline.py`): S0~S14를 계약 스텁으로 연결, 벤치 v0 데이터셋에서 종주. `autoetl analyze <csv> --no-llm [--out report.json]` CLI 콘솔 스크립트 (T-0010)
- 벤치 정답 형식과 채점 기준 v0: `bench/answer.py`(BenchAnswer 정답·예측 형식), `bench/scoring.py`(타입 F1·PK/FK F1·FD 복원율·배정 ARI·종합 점수), `bench/criteria.py`(필수 관문 3종, 최소 85%·권장 95% 모델 판정), 명세 `bench/README.md`. 생성기 answer.json을 정식 형식으로 교체 (T-0009)
- 벤치 데이터셋 생성기 v0(`bench/generator/`): 합성 관계형 팩 2종, 시드 고정 생성, 리프→루트 조인 평면화, 잡음 변형 7종(결측·형식 혼재·앞자리 0·다중 헤더·CP949·hard), 정답 스키마·메니페스트 출력 (T-0008)
- 데이터·로그 등급 정책 v0: 문서(`docs/policy/data-log-levels.md`)와 기계 판독형 정의(`src/autoetl/security/levels.py`), 카나리 테스트 설계, 정합성 테스트 (T-0007)
- SOP v1 원본(`sop/v1.yaml`, S0~S14 단계·점검 항목·실패 시 행동·LLM 프롬프트)과 생성기(`tools/build_sop.py` → `docs/sop-v1.md`, `docs/prompts-v1.md`) (T-0006)
- 단계별 계약 v0: S0~S14 산출물 15개의 Pydantic 모델과 JSON Schema 내보내기(`tools/export_contracts.py` → `schemas/`), 계약 버전 규칙 (T-0005)
- 저장소 골격: uv 기반 `pyproject.toml`, 순수 Python 휠 구성, `src/autoetl` 패키지와 `py.typed` (T-0002)
- 품질 도구: ruff(lint·format), mypy(strict), pytest 설정과 `uv.lock` (T-0002)
- CI: Windows·macOS·Linux × Python 3.10~3.13 테스트 매트릭스, 린트·타입 검사, 의존성 라이선스 자동 검사(`tools/license_check.py`) (T-0002)
- 오픈소스 기본 파일: LICENSE(Apache-2.0), README(한·영), 기여 안내, 행동 강령, 보안 정책, 이슈·PR 템플릿 (T-0003)
