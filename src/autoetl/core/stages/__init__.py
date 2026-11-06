"""단계 모듈 규약(s00_ingest … s14_report).

각 단계는 `run(...)` 함수 하나를 내놓는다. 인자는 (컨텍스트, 필요한 앞 단계
산출물)이고 반환은 그 단계의 계약 산출물이다. pipeline.py는 순서대로 부르기만
한다. 자세한 규약은 core/README.md.
"""

from __future__ import annotations

STUB_NOTE = "v0 빈 구현"

SOP_VERSION = "v1"
"""sop/v1.yaml의 version과 같아야 한다(테스트가 대조한다). pipeline이 재수출한다."""
