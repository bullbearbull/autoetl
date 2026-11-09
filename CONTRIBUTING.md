# 기여 안내

관심 가져 주셔서 고맙다. 이 저장소는 AI 에이전트가 구현하고 사람(소유자)이 핵심을 검토하는
방식으로 개발한다. 사람 기여자든 에이전트든 아래 규칙은 같다.

## 시작하기

버그는 재현 방법과 환경(OS, Python 버전, 커밋)을 담아 이슈로, 기능 제안은 문제와 제안을
담아 이슈로 먼저 남긴다. 큰 변경은 이슈에서 먼저 방향을 합의한 뒤 시작한다.

## 작업 흐름

1. 이슈(또는 과업)마다 브랜치를 하나 만든다. 과업 기반 작업은 이름에 과업 번호를 넣는다(예: `t-0005-contracts`).
2. Python 3.10 이상과 [uv](https://docs.astral.sh/uv/)로 환경을 맞춘다.

   ```bash
   uv sync
   ```

3. 아래 검사를 전부 통과시킨다.

   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy
   uv run pytest
   ```

4. PR을 올린다. 템플릿을 채운다. CI(Windows·Linux·macOS × Python 3.10~3.13) 전 항목 통과가 병합 조건이다.

## 규칙

- **새 의존성**: 핵심 패키지는 허용형 라이선스(MIT·BSD·Apache-2.0·PSF·MPL-2.0 계열)만 쓴다.
  `uv run python tools/license_check.py`가 자동 검사한다. 새 의존성 추가는 유지자 검토가 필요하니 PR에 명시한다.
- **검토 관문**: 계약(contracts), SOP, 보안 정책(Secure Mode·외부 전송·로그), 벤치 판정 기준,
  공개 API(SDK·CLI) 시그니처, 릴리스는 사람이 검토한 뒤 병합한다.
- **커밋 메시지**: 게시판 과업 작업은 `T-XXXX: 무엇을 했는지` 형식을 쓴다. 일반 기여는 변경을 요약하는 한 줄로 충분하다.
- **기여 서명(DCO, 권장안)**: 커밋에 `git commit --signoff`로 `Signed-off-by:`를 남긴다.
  최종 기여 방식(DCO/CLA)은 아직 결정 전이며 확정되면 이 문서를 고친다.
- **비밀과 실데이터**: 실제 데이터 값, API 키, 개인정보를 이슈·PR·로그·테스트에 넣지 않는다.
  벤치 데이터는 생성 스크립트로 만든 합성 데이터만 쓴다.
- **플랫폼 범위**: Windows·macOS·Linux, Python 3.10 이상에서 모두 동작해야 한다. 경로는 `pathlib`, 파일은 UTF-8.

## 행동 강령

[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)(Contributor Covenant 2.1)를 따른다.
