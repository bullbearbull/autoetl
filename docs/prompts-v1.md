# LLM 프롬프트 v1

갱신 2026-11-05 · 원본 `sop/vv1.yaml` (생성물 — 직접 고치지 않는다)

모든 LLM 호출에는 단계 브리프 공통 머리가 붙고 과업별 프롬프트가 이어진다.
`{{ }}` 자리는 하네스가 채운다.

## 단계 브리프 공통 골격

```text
[Auto ETL SOP {{sop_version}} · {{step_id}} {{step_name}}]
현재 위치: {{position}}
이전 결정 요약: {{prior_decisions}}
이번 과업: (과업 프롬프트)
출력 스키마: (계약 JSON Schema)
점검 항목: (단계 점검 항목)
```

브리프에 들어가는 정보: 현재 위치, 이전 결정 요약, 이번 과업, 출력 스키마, 점검 항목

## S3 타입 추론 — T-TYPE-RESOLVE

역할: 임계치 미달 컬럼의 의미 타입 확정

```text
이번 과업: 컬럼 "{{column}}"의 프로파일 요약을 읽고 의미 타입 후보 중 하나를 고른다.
후보 목록(이것만 허용): {{candidates}}
프로파일 요약: {{profile_summary}}
형태 마스크 상위: {{top_patterns}}
출력 스키마: type_assignment (JSON Schema 준수, evidence 필수)
점검 항목:
- evidence에는 받은 프로파일에 실제 있는 ID만 인용한다.
- 소수점·콤마·통화 기호가 보이면 continuous/amount 계열만 고른다. integer 금지.
- 확신이 낮으면 alternatives에 차선을 남기고 confidence를 낮춘다.
```

## S8 의미 해석 — T-NAME, T-ENTITY-GROUP

역할: 의미 해석 전체 (T-NAME은 컬럼 단위, T-ENTITY-GROUP은 묶음 단위)

```text
이번 과업: 컬럼 의미 해석과 표준 영문 이름, 엔터티 묶음을 만든다.
컬럼 정보: {{column_block}} (이름·의미 타입·프로파일 요약)
FD 기반 묶음 후보: {{entity_candidates}}
도메인 사전 후보: {{domain_dict_candidates}}
출력 스키마: semantic_model (JSON Schema 준수, evidence 필수)
점검 항목:
- 표준 이름은 ^[a-z][a-z0-9_]*$ 를 지킨다. 원본 이름과 다르면 뜻을 유지한다.
- 엔터티는 FD 결과와 모순되지 않게 고른다. 근거 ID를 인용한다.
- 추측이 섞이면 questions에 사용자 질문을 만든다. 지어내지 않는다.
```

## S9 후보 스키마 생성 — T-CANDIDATE-REVIEW

역할: 후보 보정 (후보·점수표를 받아 허용 연산만 적용)

```text
이번 과업: 후보 스키마를 허용 연산 안에서만 보정한다.
후보: {{candidate_summary}} (테이블·컬럼·근거)
점수표: {{scorecard_summary}}
허용 연산: rename, change_type, split, merge, add_index (이것만 가능)
출력 스키마: schema_candidate (source는 "code+llm")
점검 항목:
- 원본 컬럼이 사라지거나 중복되지 않게 보정한다 (무손실 유지).
- 근거 없는 이름·타입 변경은 하지 않는다. 이유를 rationale에 남긴다.
- 점수표의 낮은 항목만 골라 고친다. 전면 재작성 금지.
```

## S11 권고 — T-RECOMMEND

역할: 순위·사유 작성

```text
이번 과업: 점수표를 읽어 후보 순위와 추천 사유를 쓴다.
점수표: {{scorecard_summary}} (항목별 점수·근거)
후보 요약: {{candidate_summaries}}
출력 스키마: recommendation (JSON Schema 준수, reasons에 Evidence 필수)
점검 항목:
- 가중합 점수와 순위가 어긋나면 점수를 따른다. 임의 재평가 금지.
- 모든 사유에 Scorecard:... 인용을 붙인다. 없는 근거 금지.
- tradeoffs는 사용자가 이해할 업무 언어로 쓴다.
```

## S13 적재 플랜 — 없음(보조)

역할: 변환 규칙 설명·증분 전략 추천 보조 (판단은 코드가 소유)

```text
이번 과업: 적재 플랜의 변환 규칙 설명과 증분 전략 추천안을 쓴다.
선택 스키마: {{selected_summary}}
품질 이슈: {{quality_issues}}
출력 스키마: load_plan의 transform 설명·increment 권고 구간 (최종 확정은 코드)
점검 항목:
- 품질 이슈(Q-XXXX)와 모순되는 변환을 제안하지 않는다.
- upsert 키 후보는 실제 유일성(FD·후보 키) 근거만 인용한다.
```
