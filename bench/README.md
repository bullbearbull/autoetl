# 벤치마크 v0 — 정답 형식, 채점 기준, 모델 판정

기준일 2026-11-05 · 근거: docs/PLAN.md 벤치마크 절 · 코드: `bench/answer.py`,
`bench/scoring.py`, `bench/criteria.py`, `bench/generator/`

이 문서의 판정 수치와 가중치는 초기 제안값이고, 변경은 소유자 검토 대상이다
(AGENTS.md 3절 — 벤치 판정 기준과 최소·권장 모델).

## 1. 데이터셋 구성

```
bench/datasets/<팩>-<시드>/
  clean.csv           잡음 없는 평면 원본
  missing.csv         결측 주입('-', 'N/A', '해당없음')
  format_mix.csv      날짜 4형식·천 단위 콤마·'원' 표기 혼재
  leading_zero.csv    고정 자릿수 코드의 앞자리 0 일부 제거
  multi_header.csv    두 행 헤더(그룹 | 필드)
  cp949.csv           CP949 인코딩
  hard.csv            위 잡음 전부 + CP949
  answer.json         정답 스키마(BenchAnswer)
  manifest.json       시드·변형·라이선스·규모
  README.md           사람용 설명(라이선스 포함)
```

생성: `uv run python -m bench.generator --seed 42`. 생성물은 커밋하지 않는다.
v0 팩은 전부 합성 데이터다(라이선스 제약 없음). 실제 공개 DB(Chinook·Northwind·
Sakila) 수입은 데이터별 이용 조건 확인 후 v1에 추가한다.

## 2. 정답 형식 (answer.json, `bench/answer.py`)

- `answer_version`: 형식 버전(0.1.0).
- `tables[]`: 테이블마다 `name`, `primary_key`(컬럼 목록), `columns[]`
  (`name`, `physical_type` — contracts.PhysicalType, `is_foreign_key`),
  `foreign_keys[]` (`table`, `columns`, `references_table`, `references_columns`),
  `functional_dependencies[]` (`table`, `determinant[]`, `dependent`),
  `row_count`.
- v0 FD는 원본 PK → 비키 컬럼 형태만 담는다.

**채점 키 규칙(v0 합의)**: 채점용 예측(`BenchPrediction`)의 컬럼 이름은 평면
입력 파일의 헤더 이름, 곧 정답의 `테이블_컬럼`을 그대로 보존한다. 예측 테이블
이름은 채점에 쓰지 않는다.

## 3. 지표 계산식 (`bench/scoring.py`)

| 지표 | 계산 |
| --- | --- |
| 타입 F1 | 컬럼 단위 물리 타입 일치의 정밀도·재현율 F1(타입을 틀린 컬럼은 정밀도·재현율 모두에서 감점) |
| PK F1 | PK 컬럼 집합이 같은지로 매칭한 F1 |
| FK F1 | (자식 컬럼 집합, 부모 컬럼 집합) 쌍 일치 F1 |
| FD 복원율 | 정답 FD 중 예측이 찾은 비율(재현율, PLAN 지표) |
| FD F1 | FD 정밀도·재현율 F1 — 모든 컬럼 쌍을 FD로 나열하는 스팸 예측을 막는다 |
| 배정 ARI | 컬럼→테이블 배정의 조정 랜드 지수(1.0 완전 일치, 0 무작위 수준). 겹치는 컬럼이 1개 이하는 자명 일치(1.0) |
| 테이블 수 오차 | 예측 테이블 수 − 정답 테이블 수 |

스키마 복원 종합 점수(0~100) = 다섯 지표(타입 F1·PK F1·FK F1·**FD F1**·ARI)의
**균등 가중** 평균 × 100(FD는 복원율이 아니라 F1을 넣는다). DDL 성공률·적재
성공률·데이터 손실률·안정성(N회 실행 스키마 차이)·JSON 준수율·카나리 유출·비용·
자원은 실행기(런너, P5)가 실측값을 채운다.

## 4. 필수 관문 (`bench/criteria.py`)

관문을 통과하지 못한 모델은 판정 대상에서 제외된다.

| 관문 | 기준 |
| --- | --- |
| DDL 성공 | 시험 적재·DDL 성공률 **100%** |
| 보안 | Secure 모드 카나리 유출 **0건** |
| 출력 형식 | JSON 준수율 **99% 이상** |

## 5. 최소·권장 모델 판정

- **기준선(best score)**: 전체 모델 가운데 최고 종합 점수. 관문을 통과하지 못한
  프런티어 모델이 최고점이면 그 점수가 기준선이 된다(관문 실패는 후보 자격만
  박탈한다). 판정 대상(후보)은 관문을 통과한 모델만이다.
- **최소 모델**: 기준선의 **85% 이상** 점수를 가진 후보 중 파라미터가 가장 작은 것.
- **권장 모델**: 기준선의 **95% 이상** 점수를 가진 후보 중 점수/비용 지표가 가장
  좋은 것(비용 지표는 0보다 커야 한다).
- 후보가 없거나 기준선에 닿는 후보가 없으면 판정 보류(없음).

두 비율(85%·95%)은 PLAN의 초기 제안값이며, 하네스 목표(소형 모델 점수가
프런티어의 90% 이상)와 함께 상시 벤치 결과로 조정한다. 균등 가중·기준선 해석은
후속 T-0012(A/B 검증)에서 실측으로 재확인한다.
