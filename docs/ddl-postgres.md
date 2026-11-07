# PostgreSQL DDL 타입 매핑(T-0030)

`src/autoetl/core/ddl/postgres.py`가 따르는 매핑 규칙. 이 표와 코드가 어긋나면
테스트(`tests/test_ddl_postgres.py`)가 실패한다. DDL은 sqlglot 표현식 트리로
만들고(문자열 이어 붙이기 없음), 식별자는 항상 큰따옴표로 인용한다.

## 물리 타입 → PostgreSQL 타입

관측값 없이 정해지는 타입(정적 매핑 — `TYPE_MAP`):

| 물리 타입 | PostgreSQL |
| --- | --- |
| boolean | BOOLEAN |
| date | DATE |
| time | TIME |
| timestamp | TIMESTAMP |
| timestamptz | TIMESTAMPTZ |
| json | JSONB |
| binary | BYTEA |
| float | DOUBLE PRECISION |

관측값으로 정하는 타입(동적 매핑):

| 물리 타입 | PostgreSQL | 규칙 |
| --- | --- | --- |
| integer | INT 또는 BIGINT | 전수 관측 최솟값·최댓값(S4 수치 통계)이 모두 int4 범위(-2147483648~2147483647) 안이면 INT, 벗어나거나 범위 미상이면 BIGINT(안전 쪽) |
| decimal | DECIMAL(p, s) | precision에 +2, scale에 +1 여유(둘 다 상한 38). 상한에 걸리면 scale 여유를 먼저 포기해 정수부 자릿수(p−s)와 scale이 원래 값보다 줄지 않게 한다. precision이 없으면 무제한 DECIMAL |
| string | VARCHAR(n) 또는 TEXT | 전수 관측 최대 길이(S2 프로파일)에 +20 여유. 길이를 모르거나 여유를 더한 값이 varchar 상한(10485760)을 넘으면 TEXT |

관측 한도는 전수일 때만 쓴다. `generate_ddl(..., sampling=SamplingPlan)`의
strategy가 "full"일 때만 관측값을 반영하고, 표본("sample")이거나 계획이
없으면 string은 TEXT, integer는 BIGINT다(실패 닫힘 — 표본 최댓값은 전수
최댓값보다 작을 수 있어 한도로 쓰면 적재가 깨진다. T-0025 규칙 '표본은
판정에만 쓴다'와 같다).

참고: INT는 INTEGER의 PostgreSQL 표준 동의어다. sqlglot postgres 생성기가
INTEGER를 INT 표기로 정규화하므로 생성 DDL에는 INT로 나온다(의미 차이 없음).

참고: DECIMAL은 PostgreSQL에서 NUMERIC의 표준 동의어다. sqlglot postgres
생성기가 NUMERIC을 DECIMAL 표기로 정규화하므로 생성 DDL에는 DECIMAL(p, s)로
나온다(의미 차이 없음).

## 여유·상한 설정

| 설정 | 값 |
| --- | --- |
| numeric precision 여유 | 2 |
| numeric scale 여유 | 1 |
| numeric 정밀도 상한 | 38 |
| varchar 길이 여유 | 20 |
| varchar 상한(초과 시 TEXT) | 10485760 |
| 식별자 길이 상한(바이트) | 63 |
| 이름 축소 해시 접미(sha256 16진수) | 8 |
| int4 상한 | 2147483647 |
| int4 하한 | -2147483648 |

## 식별자

- 항상 큰따옴표로 인용한다(안의 큰따옴표는 이중 이스케이프) — 어떤
  이름이든 추가 구문이 끼어들 수 없다(재파싱 대조로 확인).
- 63바이트(PostgreSQL NAMEDATALEN-1, UTF-8 기준)를 넘는 이름은 앞부분 +
  `_` + sha256 앞 8자리로 결정론적으로 줄인다. 축소 결과가 서로 다른 이름에서
  겹치면 오류(실패 닫힘).

## 제약·순서

- PK는 `is_primary_key` 컬럼들의 테이블 레벨 PRIMARY KEY(복합 포함).
- FK는 `foreign_key`('테이블(컬럼)') 참조 — 참조 컬럼은 부모의 PK여야 한다
  (이 생성기는 UNIQUE 제약을 만들지 않는다). 부모 PK가 단일 컬럼이면 자식
  컬럼마다 FK를 따로 낸다(홈·원정 팀처럼 같은 부모를 향하는 독립 FK가 복합으로
  묶이지 않는다). 부모 PK가 복합이면 PK 컬럼 전부를 한 번씩 가리킬 때만 하나의
  복합 FOREIGN KEY로 묶는다(자식 컬럼 순서 = 테이블 정의 순서). 빠지거나
  겹치면 오류다.
- FK 자식 컬럼의 물리 타입은 부모 컬럼과 같아야 하고(다르면 오류), PostgreSQL
  타입도 부모와 같은 것으로 낸다(관측값이 달라 각자 정하면 INT·BIGINT가
  섞인다).
- `nullable=False` 컬럼에 NOT NULL.
- 테이블은 FK 의존 순서(부모 먼저, 동률은 이름순)로 낸다. FK 순환은 오류.
- 스키마 이름은 `generate_ddl(..., schema=...)` 옵션(63바이트 이하) —
  REFERENCES 테이블도 같은 스키마로 한정한다(search_path 오남용 방지).
- 같은 후보·프로파일·통계는 같은 DDL 바이트를 낸다(결정론).
- `verify_ddl`은 문장 수·테이블·컬럼·PK·FK(스키마 한정 포함)·컬럼별
  NOT NULL을 후보와 대조한다. PK 컬럼은 PostgreSQL이 NOT NULL로 다루므로
  명시 여부는 물지 않는다.
