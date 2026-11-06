# core — 결정론 엔진

P1 단계별 구현 규칙. 전체 규칙은 저장소 AGENTS.md와 docs/PLAN.md P1을 따른다.

## 구성

- `context.py` — `AnalysisContext`: 원본 판독(헤더·행 수·크기), DuckDB 연결,
  원본 테이블(`raw`)·내부 식별자 뷰(`raw_view`), 임시 폴더 수명.
- `stages/` — 단계 모듈 `s00_ingest.py` … `s14_report.py`. SOP v1의 S0~S14와
  파일이 일대일 대응한다.
- `pipeline.py` — 컨텍스트 생성과 순서 호출·`PipelineResult` 조립만 한다.

## 단계 모듈 규약

- 각 모듈은 `run(...)` 함수 하나를 내놓는다. 인자는 **(컨텍스트, 필요한 앞 단계
  산출물)**, 반환은 그 단계의 계약 산출물이다. 예:

  ```python
  def run(ctx: AnalysisContext, ingest: IngestManifest) -> list[ColumnProfile]:
  ```

- 단계는 결정론이어야 한다: 같은 입력(과 시드)이면 같은 산출물. LLM·네트워크·
  시간·난수를 쓰지 않는다.
- 앞 단계 산출물은 **함수 인자**로 받는 게 기본 인터페이스다. 파이프라인이
  `ctx.stage_artifacts["s00_ingest"]`에도 등록하니 부수 접근이 필요하면 이
  레지스트리를 쓸 수 있다(형은 `object`라 필요한 계약으로 좁혀 쓴다).
- 컬럼 참조는 소스 이름(S0의 `#N` 유일화 이름)으로 계약 산출물을 쓰고, 원본
  데이터 조회는 내부 식별자(`ctx.columns[i].column_id`, `c0`·`c1`…)로
  `raw_view`에 쿼리한다. 위치가 곧 신원이라 중복 헤더·특수문자와 무관하다.
- 원본 값은 바꾸지 않는다(`raw`는 전 값 VARCHAR). 정규화 결과는 `raw` 옆에
  새 뷰를 만들고 그 이름을 `ctx.normalized_view`에 남긴다(S3 담당).
- 임시 파일·폴더는 컨텍스트 임시 폴더(`DuckDB temp_directory`) 안에만 만들고
  단계가 직접 지우지 않는다 — 컨텍스트가 실행 끝(예외 포함)에 정리한다.
- 셀 값은 예외·경고·로그와 계약의 notes·warnings에 넣지 않는다(정책 문서 3절
  금지 패턴). 컬럼 이름·번호·집계값은 가능하다.
- 오류는 `InputError`(입력 문제, 안내 문구에 값 없음) 또는 일반 예외(내부
  오류 — CLI가 고정 문구로 감싼다)로만 올린다.

## 자기 단계만 고친다

단계 과업은 자기 단계 모듈과 그 테스트만 고친다. `pipeline.py`는 연결 부분,
`context.py`·계약은 별도 과업이다. 산출물 형태(계약)를 바꿔야 하면 과업 카드에
`-k question`으로 먼저 올린다(계약 버전 규칙).
