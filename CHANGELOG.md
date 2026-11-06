# Changelog

이 문서는 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고
[Semantic Versioning](https://semver.org/lang/ko/)을 사용한다.

## [Unreleased]

### Added

- 벤치 실행기 v0(`bench/runner.py`): 파이프라인 결과를 채점해 점수표를 낸다 — `uv run python -m bench.runner --seed 42 [--pack all]`. 채점 대상은 3NF 후보(pattern `3nf`)이며, 없으면 선택 후보로 대신 채점하고 표에 표시하며 선택 후보 점수도 함께 기록한다. 변환 규칙: 컬럼 이름 = source_columns의 원본 이름(그 변형의 필드 헤더), PK = is_primary_key, FK = foreign_key('테이블(컬럼)'), FD = 테이블마다 PK → 비키 컬럼. 실패한 변형은 0점 + 실패 단계·종료 코드만 기록(입력 오류 S0·종료 1, 내부 오류 단계 미상·종료 3, 원문 오류 메시지는 남기지 않는다). 산출물은 Markdown 표(stdout)와 JSON(`bench/results/`, 커밋하지 않음), 시드 고정 재현. v0 스텁 시점 종합(시드 42): orders_kr·music clean 23.03·24.23, multi_header 0.00, cp949·hard 실패 (T-0022)
- 엔진 골격(T-0021): `AnalysisContext`(`src/autoetl/core/context.py`) — csv 모듈 판독(오류 경로는 v0 러너와 동일) + 프로세스 안 DuckDB 연결 1개(메모리 한도·스레드 설정, 임시 폴더는 실행 끝 예외 포함 정리), 원본을 전 값 VARCHAR로 적재한 `raw` 테이블(컬럼 이름 = 위치 기반 내부 식별자 `c0`, `c1`, … — 파일에서 온 헤더 문자열은 SQL에 넣지 않는다). 원문 보존: 앞자리 0·지수 표기·공백 값 그대로, 따옴표 없는 빈 칸은 NULL·따옴표 빈 문자열("")은 ''로 구분(`allow_quoted_nulls=false`). 연결 잠금(심층 방어): 확장 자동 설치·자동 로드 끔, 원본 적재 후 외부 접근(파일 함수·ATTACH) 차단, 식별자 인용 공용 헬퍼 `quote_identifier`. 단계 모듈 `core/stages/s00_ingest.py` … `s14_report.py`로 분리(각 `run(ctx, 앞 산출물)` → 계약 산출물), `pipeline.py`는 순서 호출만. 산출물은 리팩터링 전과 동일(골든 Report 10종 대조, 골든은 단계 과업이 산출물을 바꿀 때 함께 갱신). 새 의존성 duckdb(MIT). 단계 규약 문서 `src/autoetl/core/README.md`(SQL에는 코드가 만든 식별자와 매개변수만). 리드 검토(#97) 반영

### Changed

- CLI 입력 오류 안내 체계: 입력 문제는 `InputError`(빈 CSV·CSV 구문 오류, 셀 값 미포함 안내)로 종료 1, UTF-8 디코딩 실패는 고정 안내로 종료 1, 그 밖의 내부 오류는 원문 없이 고정 문구로 종료 3. 이슈 안내는 실제 데이터 첨부를 요구하지 않는다(명령·버전·컬럼 수·헤더 형태만) (T-0017)
- CI macOS 검사 보류(비용 — macOS 러너는 Linux의 약 10배 요금). 매트릭스는 ubuntu·windows × Python 3.10~3.13, 되돌리는 조건을 워크플로 주석에 명시 (T-0019)

- 벤치 v0.1: 평면 헤더에서 정답 테이블 정보를 제거했다(T-0015). 헤더는 접두사 없는 컬럼 이름이고 조인 키(PK=FK)는 부모 PK 이름 하나로 처음 나오는 위치에 한 번만 나온다(orders_kr 19컬럼, music 13컬럼). 채점 키를 헤더 문자열에서 컬럼 ID로 바꿨다 — answer.json 0.2.0(`column_id`, `variant_headers`), FK 식별은 (자식 PK·FK 컬럼 ID 집합) 쌍, 배정(ARI)에서 조인 키는 PK 소유 테이블 귀속, 타입은 컬럼 ID당 1회 집계.
- 벤치 잡음이 컬럼 이름이 아니라 팩 선언의 kind·키 여부로 대상을 고른다(T-0015). PK·조인 키에는 결측을 넣지 않고, music 팩에 5자리 코드 컬럼(albums.catalog_no)을 추가해 leading_zero 변형이 두 팩 모두에서 의미 있게 했다. 다중 헤더 그룹 행은 컬럼 성격 라벨(식별자·날짜·금액 등)로 바꾸고, hard 변형은 이름 없는 필드 헤더(col_01 형식)를 쓴다.
- 기준선 예측 3종(`bench/baselines.py`: 단일 와이드·헤더 이름 규칙·'_id' 위치 끊기)과 그 점수를 bench/README.md에 기록했다(시드 42: 와이드 21.68~23.85, 이름 규칙 21.68~31.46, 위치 끊기 21.68~53.85) — 헤더 이름 신호를 쓴 최선 예측의 수준을 판정 기준점으로 삼으며 문서↔코드 일치 테스트가 대조한다 (T-0015)
- 리드 재검토 반영(T-0015): 다중 헤더 그룹 행에서 키 컬럼을 구별하지 않는다(id·fk도 '수치' — '식별자' 위치가 테이블 경계를 새던 재현 53.68점 해소), 같은 부모 PK를 다른 이름으로 참조하는 FK는 조용히 합쳐지지 않고 생성기 오류를 낸다, hard 헤더 전용 공격이 clean 이름 신호를 넘지 못하는 테스트 추가.

### Fixed

- 벤치 정답: 정수 대리 키(kind id·fk)의 정답 물리 타입을 string에서 integer로 바로잡았다(orders_kr 4컬럼, music 3컬럼). PostgreSQL 설계 기준이며 정수 키를 문자열로 설계한 예측에 점수를 주던 왜곡을 제거한다. answer.json 0.3.0, 기준선 표 재계산(단일 와이드 21.68·23.85 → 17.47·19.23, 위치 끊기 53.68·53.85 → 49.47·49.23) (T-0020)
- 채점: 같은 PK·FK·FD를 여러 번 예측해도 감점이 없던 구멍을 막았다 — 정밀도 분모를 예측 개수(중복 포함)로 하고 분자는 같은 정답을 한 번만 인정한다. 기준선(전체 모델 최고 점수)이 0 이하일 때는 최소·권장 모델 판정을 보류한다(0 ≥ 0×0.85로 무의미하게 지정되던 것 방지) (T-0016)
- 채점: 정답 FK·FD가 비어 있을 때 예측이 비어 있어야 만점이고, 스팸 FK·FD를 늘어놓으면 0점으로 바꿨다(타입 F1·PK F1·FD 복원율도 같은 규칙으로 통일). 배정 ARI에 겹침 비율(겹친 컬럼 수/정답 컬럼 수)을 곱해 컬럼 1개만 예측해도 만점이 나오던 비단조 구간을 제거 (T-0014)
- 러너: 원본 CSV 헤더에 같은 이름이 여러 개면(예: multi_header 변형) 소스 이름에 발생 접미(`#N`)를 붙이고 대상 이름을 위치 기반으로 만들어 유일화. 계약 산출물 전부(IngestManifest·LoadPlan 등)가 유일한 소스 이름을 참조한다 (T-0014)
- CLI: `--out`이 입력 파일의 하드 링크·대소문자 무시 파일시스템 표기일 때도 덮어쓰기를 거부(`samefile` 비교). 계약 위반 안내 문구가 실제 데이터를 공개 이슈에 첨부하도록 유도하지 않게 수정(명령·버전·컬럼 수·헤더 형태만 요청) (T-0014)

### Added

- 보안 정책 v0.1: 신뢰 구역 3분류(루프백·등록 사내·외부)와 구역별 원문 전송 허용 판정(`trust_zone()`·`allows_raw_value()`·`allows_metadata()`). 사내 등록은 사설 IP 대역(`PRIVATE_NETWORKS`: RFC1918 3종 + fc00::/7)만 허용하고 공인 주소·DNS 이름은 외부로 판정한다(실패 닫힘). 데이터 L3 원문은 어떤 모드에서도 루프백만 받는다. 프롬프트·응답 원문의 로그 등급은 담긴 값의 최고 등급을 따른다(`prompt_log_level()`): 데이터 L2·L3 원문 → 로그 L3, L0·L1 원문만 → 로그 L2, 메타데이터만 → 로그 L1. 문서 1.2·1.3·2.2절 갱신, 구역별 허용 표↔코드 전조합 대조 테스트. 리드 검토(#84) 반영: 비밀값 포함 표시(→ 로그 L3), 응답 등급 하한(요청 프롬프트 등급 이상, `at_least`), 루프백 판정 한계(로컬 프록시·포트 전달 경유) 문서 명시 (T-0018)
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
