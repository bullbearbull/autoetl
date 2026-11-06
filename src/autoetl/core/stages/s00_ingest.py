"""S0 수집·정규화 — 입력 추론(probe_source)과 IngestManifest.

추론 규칙(리드 결정, T-0023):

- 인코딩: BOM 확인 → UTF-8 엄격 디코딩 → 실패하면 **CP949 우선 규칙**으로
  cp949 엄격 전체 디코딩을 먼저 시도한다(성공하면 cp949). 한국
  공공데이터(CP949)가 핵심 대상이라 한글이 드문 파일(ASCII가 대부분)이
  1바이트 코드페이지(cp1250 등)로 골라져 조용히 깨지는 일을 막는다 —
  cp949는 2바이트 코드페이지라 엄격 디코딩 성공 자체가 강한 근거고,
  서유럽 표본(불·독·포·폴란드어)은 cp949로 끝까지 읽히지 않는다(리드
  확인). 그다음 charset-normalizer 감지를 한다(CP949·EUC-KR 주의).
  감지 표본은 UTF-8 디코딩이 실패한 위치 근처 바이트다 — 한국
  공공데이터처럼 앞부분이 ASCII이고 한글이 뒤에 처음 나오는 CP949
  파일도 잡는다(앞 64KB를 함께 넣으면 ASCII가 표본을 지배해 근처
  코드페이지가 이겨버려서 재현 후 버렸다). 상위 후보는 전체 파일 증분
  디코딩으로 검증해 끝까지 읽히는 첫 후보를 고른다. 후보가 1바이트
  코드페이지(latin·cp125x 계열)면 경고를 남긴다 — 거의 모든 바이트를
  읽어버려 '전체 파일 검증'이 근거가 되지 못하고 다른 인코딩과 구분이
  안 되기 때문이다(대화 모드에서는 이 경고로 사용자 확인을 받는다).
  신뢰도가 낮으면 경고를 남기고 비대화 모드 SOP대로 latin-1으로 다시
  읽는다. UTF-8이 아니면 임시 UTF-8 파일로 변환해 적재하고 컨텍스트가
  실행 끝(예외 포함)에 지운다. CP949 우선 규칙의 대가: GBK·Big5 같은
  다른 2바이트 코드페이지 표본도 cp949로 끝까지 읽혀 경고 없이 cp949로
  판정될 수 있다. 한국 데이터 우선 제품이라 받아들인 것이고, 인코딩을
  직접 지정하는 옵션은 이후 CLI 과업에서 다룬다(리드 확인).
- 구분자: 앞부분 표본(첫 50줄)에서 후보(쉼표·세미콜론·탭·파이프)를 골라
  표본의 필드 수 일관성으로 확정한다. 인용 필드 안의 구분자는 csv 모듈이
  처리한다.
- 헤더 행 수: **기본은 헤더 1행이다.** 2행 이상이나 0행으로 판정을 바꾸려면
  충분한 근거가 필요하다 — 데이터 표본의 강형 컬럼(정수·소수·날짜 다수)이
  2개 이상이고, 그 칸에 값이 있는(빈 칸과 흔한 결측 표기는 헤더 근거로
  세지 않는다) 위쪽 연속 행의 값이 텍스트 형태일 때만 헤더로 센다(비율
  0.5 이상, 최대 5행). 다중 행 헤더면 컬럼 이름은 마지막 헤더 행(필드
  행)이고 applied_normalizations에 남긴다. 첫 행이 데이터 형태면 헤더
  없음(0행)은 강형 칸 전부가 데이터의 세부 형태(정수·소수·날짜)와 같고
  값 자릿수까지 데이터 범위 안일 때만 인정한다 — 자릿수 범위는 표본에서
  첫 행을 뺀 나머지 전체로 잡고(앞쪽 작은 값이 빠지면 커지는 순서
  데이터를 헤더로 잘못 본다) 숫자 강형 컬럼 **과반**에서 벗어나야 헤더
  근거다(한 컬럼만 어긋나는 것은 일련번호 같은 커지는 순서 데이터다).
  연도형 숫자 헤더('2020', '2021'…)는 소수 데이터와 세부 형태로, 인구 같은
  큰 정수 데이터와는 자릿수로 구분돼 1행을 유지하며, 자릿수 근거만으로
  1행을 정했으면 확인 요청 경고를 남긴다. 헤더가 없다고 판정하면
  col_1..N으로 이름 짓고 경고를 남긴다
  (SOP). 낮은 근거로 판정을 바꾸면 데이터 행이 사라지고 셀 값이 컬럼
  이름(로그·LLM 메타데이터로 나가는 값)이 되기 때문이다.
- 이름 중복은 T-0014 규칙(#N 접미)을 유지한다.

메모리: probe는 파일 크기와 무관하게 돈다. 인코딩 검사·변환은 증분
디코더(청크 1MB)로 스트리밍하고, 구분자·헤더·형태 프로파일은 앞부분
표본(헤더 검사 구간 5행 + 프로파일 200행)으로 정한다. 행 수는 레코드를
저장하지 않고 세기만 한다. skip_lines는 헤더가 끝나는 물리 줄 위치
(빈 줄·인용 줄바꿈 포함 정확한 csv 줄 번호)라 적재가 헤더를 데이터로
읽지 않고, 적재 뒤 컨텍스트가 raw 행 수와 대조한다.
"""

from __future__ import annotations

import codecs
import csv
import itertools
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from charset_normalizer import from_bytes

from autoetl.contracts import IngestFormat, IngestManifest, SourceColumn, SourceFile
from autoetl.core.errors import InputError

if TYPE_CHECKING:
    from autoetl.core.context import AnalysisContext

DELIMITER_CANDIDATES = (",", ";", "\t", "|")
"""구분자 후보. 이 집합 안의 글자만 SQL에 안전하게 들어간다."""

DELIMITER_SAMPLE_LINES = 50
HEADER_SCAN_LIMIT = 5
PROFILE_SAMPLE_ROWS = 200
SAMPLE_RECORDS = HEADER_SCAN_LIMIT + PROFILE_SAMPLE_ROWS
HEADER_TEXT_RATIO = 0.5
MIN_STRONG_COLUMNS = 2
MIN_HEADER_EVIDENCE = 2
ENCODING_SAMPLE_BYTES = 64 * 1024
SNIFF_BACKTRACK_BYTES = 4096
ENCODING_CANDIDATES = 5
LOW_CONFIDENCE = 0.7
CHUNK_BYTES = 1024 * 1024
CONVERT_CHARS = 1024 * 1024
SINGLE_BYTE_MIN_BYTES = 200
"""1바이트 코드페이지 판정 문턱(바이트 256개 중 혼자 디코딩되는 개수 하한).

cp125x·latin·mac 계열은 224~256개가 혼자 읽히지만 다중 바이트 코드페이지는
ASCII만 혼자 읽힌다(shift_jis는 반각 가타카나까지 합쳐 191개) — 200은
그 사이를 가른다.
"""

MISSING_MARKERS = frozenset(
    {"-", ".", "?", "n/a", "na", "null", "none", "nan", "없음", "해당없음", "미상"}
)
"""헤더 근거에서 제외할 흔한 결측 표기(소문자 기준, 대소문자 무시).

결측 값 해석 전체는 S2가 맡는다. 이 목록은 '헤더 근거 제외'에만 쓴다 —
결측 표기가 강형 칸 2개 이상을 채운 첫 데이터 행이 헤더로 판정돼 행이
사라지고 셀 값이 컬럼 이름(로그·LLM 메타데이터로 나가는 값)이 되는
누출 경로를 막는다(리드 재검토 #119).
"""

_DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")
_BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)


@dataclass(frozen=True)
class SourceSpec:
    """probe_source의 결과 — 컨텍스트 적재와 S0 산출물이 함께 쓴다."""

    original_path: Path
    load_path: Path  # DuckDB가 읽는 경로(변환됐으면 임시 UTF-8 파일)
    encoding: str  # 감지해 사용한 인코딩(SourceFile.encoding)
    delimiter: str
    header_row_count: int
    skip_lines: int  # DuckDB read_csv가 건너뛸 물리 줄 수(헤더가 끝나는 줄)
    header: list[str]  # 마지막 헤더 행 기준 이름(strip·빈칸 col_N 채움)
    row_count: int  # 데이터 행 수(헤더 제외)
    size_bytes: int
    applied_normalizations: list[str]
    warnings: list[str]
    converted: bool  # load_path가 임시 파일이면 True(컨텍스트가 삭제)


def deduplicate_source_names(header: list[str]) -> list[str]:
    """겹치는 원본 컬럼 이름에 발생 순서 접미(#N)를 붙여 유일하게 만든다.

    CSV 헤더는 같은 이름을 여러 개 가질 수 있고(벤치 v0의 multi_header 변형이
    그렇다), 그대로 두면 LoadPlan.source_column 같은 이름 참조가 둘을 구분하지
    못한다. 첫 등장은 원본 이름을 유지하고 두 번째부터 '#2', '#3'…를 붙인다.
    접미를 붙인 이름조차 실제 헤더와 겹치면 다음 접미로 밀어 유일성을 지킨다.
    """
    names: list[str] = []
    taken: set[str] = set()
    occurrences: dict[str, int] = {}
    for name in header:
        count = occurrences.get(name, 0) + 1
        occurrences[name] = count
        candidate = name if count == 1 else f"{name}#{count}"
        while candidate in taken:
            count += 1
            candidate = f"{name}#{count}"
        taken.add(candidate)
        names.append(candidate)
    return names


def sanitize_names(names: list[str]) -> list[str]:
    """표준 영문 이름(^[a-z][a-z0-9_]*$) 목록. 입력과 같은 길이·순서를 지킨다.

    ASCII가 아닌 글자(한글 헤더 등)는 버리고, 비면 col_N으로 채운다.
    충돌(예: 'foo-bar'와 'foo bar')은 숫자 접미로 유일하게 만든다.
    위치(인덱스) 기반으로 만들기 때문에 원본 이름이 겹쳐도 대상 이름은 유일하다.
    """
    safe: list[str] = []
    taken: set[str] = set()
    for index, name in enumerate(names):
        cleaned = "".join(ch if ch.isascii() and ch.isalnum() else "_" for ch in name.lower())
        parts = [part for part in cleaned.split("_") if part]
        base = "_".join(parts)
        if not base or not base[0].isalpha():
            base = f"col_{index + 1}"
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f"{base}_{suffix}"
            suffix += 1
        taken.add(candidate)
        safe.append(candidate)
    return safe


def _open_text(path: Path) -> TextIO:
    """텍스트 스트리밍 판독용 핸들. utf-8-sig라 원본 UTF-8 BOM은 뗀다.

    newline=""은 줄바꿈 변환을 끈다(원문 보존). 크기 상한 없이 이터레이션하면
    한 줄씩 흘러 나온다.
    """
    return open(path, encoding="utf-8-sig", newline="", errors="strict")


def _bom_encoding(path: Path) -> str | None:
    with open(path, "rb") as source:
        head = source.read(4)
    for bom, encoding in _BOM_ENCODINGS:
        if head.startswith(bom):
            return encoding
    return None


def _decodes_as(path: Path, encoding: str) -> bool:
    """증분 디코딩으로 끝까지 엄격하게 읽히는지만 본다(메모리는 청크 수준)."""
    decoder = codecs.getincrementaldecoder(encoding)()
    with open(path, "rb") as source:
        while chunk := source.read(CHUNK_BYTES):
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError:
                return False
    try:
        decoder.decode(b"", True)
    except UnicodeDecodeError:
        return False
    return True


def _is_single_byte_codec(encoding: str) -> bool:
    """1바이트 코드페이지(latin·cp125x 계열)인가.

    바이트 256개를 각각 혼자 디코딩해 본다. 1바이트 코드페이지는 거의 모든
    바이트를 읽어버려 '전체 파일이 읽히는가' 검증이 근거가 되지 못한다
    (어떤 파일이든 거의 통과한다). 그래서 이 판정에 걸리면 경고를 남긴다.
    """
    decoded = 0
    for value in range(256):
        try:
            bytes((value,)).decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        decoded += 1
    return decoded >= SINGLE_BYTE_MIN_BYTES


def _utf8_failure_position(path: Path) -> int:
    """UTF-8 엄격 검사. 실패하면 실패한 바이트 위치, 성공하면 -1.

    위치는 증분 디코더의 예외 start(청크 상대)를 청크 오프셋에 더한
    근사값이다 — 감지 표본에 넉넉한 앞뒤 여유를 두기 때문에 몇 바이트
    오차는 흡수된다.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    offset = 0
    with open(path, "rb") as source:
        while chunk := source.read(CHUNK_BYTES):
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError as exc:
                return offset + exc.start
            offset += len(chunk)
    try:
        decoder.decode(b"", True)
    except UnicodeDecodeError as exc:
        return offset + exc.start
    return -1


def _sniff_sample(path: Path, failure_position: int) -> bytes:
    """감지 표본: UTF-8 검사가 실패한 위치 근처 바이트.

    앞부분만 보면 ASCII로 보이는 CP949 파일(한글이 뒤에 처음 나온다)을
    잡기 위해 디코딩이 깨진 지점의 바이트를 표본에 넣는다. 앞 64KB를 함께
    넣으면 ASCII가 표본을 지배해 유럽·중국 코드페이지(cp1250·big5)가
    이겨버려서(재현 확인), 실패 위치 주변만 쓴다.
    """
    with open(path, "rb") as source:
        source.seek(max(0, failure_position - SNIFF_BACKTRACK_BYTES))
        return source.read(SNIFF_BACKTRACK_BYTES + ENCODING_SAMPLE_BYTES)


def _convert_to_utf8(path: Path, encoding: str) -> Path:
    """스트리밍 변환한 UTF-8 임시 파일. 실패해도 남지 않게 probe가 관리한다.

    newline=""로 써서 줄바꿈을 그대로 둔다 — Windows 텍스트 모드 기본값은
    '\\n'을 '\\r\\n'으로 바꿔 원본의 '\\r\\n'이 '\\r\\r\\n'이 되고 적재가 깨진다.
    """
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - 경로를 넘겨야 해서 직접 닫는다
        "w", encoding="utf-8", newline="", suffix=".autoetl-utf8.csv", delete=False
    )
    try:
        with open(path, encoding=encoding, newline="", errors="strict") as source:
            while chunk := source.read(CONVERT_CHARS):
                handle.write(chunk)
        handle.close()
    except BaseException:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise
    return Path(handle.name)


def _detect_encoding(path: Path) -> tuple[str, Path, bool, list[str]]:
    """인코딩을 감지해 (encoding, load_path, converted, warnings)을 돌려준다."""
    warnings: list[str] = []
    bom = _bom_encoding(path)
    if bom is not None and _decodes_as(path, bom):
        if bom == "utf-8-sig":
            # UTF-8 BOM: 원본을 그대로 쓴다(헤더 판독 때 utf-8-sig로 뗀다).
            return "utf-8", path, False, warnings
        return bom, _convert_to_utf8(path, bom), True, warnings
    if bom is not None:
        warnings.append(f"BOM이 {bom}을(를) 가리키지만 엄격 디코딩에 실패했다")

    failure = _utf8_failure_position(path)
    if failure < 0:
        return "utf-8", path, False, warnings

    # CP949 우선 규칙(리드 재검토 #119): 1바이트 코드페이지 후보는 '전체
    # 파일이 읽히는가' 검증으로 구분되지 않는다(거의 다 통과한다). 그래서
    # 감지기보다 먼저 cp949 엄격 전체 디코딩을 시도한다 — 한글이 1~2개뿐인
    # CP949(ASCII가 대부분)가 cp1250으로 골라져 값이 조용히 깨지던 것을
    # 막는다. cp949는 2바이트 코드페이지라 엄격 디코딩 성공이 강한 근거고,
    # 서유럽 표본(불·독·포·폴란드어)은 cp949로 끝까지 읽히지 않는다.
    if _decodes_as(path, "cp949"):
        return "cp949", _convert_to_utf8(path, "cp949"), True, warnings

    # charset-normalizer는 정돈된 짧은 텍스트에서 coherence가 0으로 나올 수
    # 있다(벤치 cp949가 그렇다). 순위는 from_bytes가 이미 정했으니 신뢰도는
    # coherence와 chaos(낮을수록 좋음)를 합쳐 잰다. 표본이 짧으면 순위
    # 1위가 근처 코드페이지(cp1250 등)로 흔들릴 수 있어, 상위 후보를 전체
    # 파일 증분 디코딩으로 검증해 끝까지 읽히는 첫 후보를 고른다.
    candidates = [
        match
        for match in list(from_bytes(_sniff_sample(path, failure)))[:ENCODING_CANDIDATES]
        if max(match.coherence, 1.0 - match.chaos) >= LOW_CONFIDENCE
    ]
    encoding = "latin-1"  # SOP(비대화 모드): latin-1은 어떤 바이트든 읽는다.
    verified: str | None = None
    for match in candidates:
        if _decodes_as(path, match.encoding):
            verified = match.encoding
            break
    if verified is not None:
        encoding = verified
        # 1바이트 코드페이지는 어떤 파일이든 거의 읽어버려 검증이 근거가
        # 되지 못한다. 다른 인코딩과 구분이 안 되니 확인 요청 경고를 남긴다
        # (대화 모드의 사용자 확인 재료).
        if _is_single_byte_codec(encoding):
            warnings.append(
                f"1바이트 코드페이지 {encoding}(으)로 읽었다. "
                "다른 인코딩일 수 있으니 확인이 필요하다"
            )
    elif candidates:
        warnings.append("감지한 인코딩으로 전체 파일을 읽지 못해 latin-1으로 읽었다")
    else:
        warnings.append("인코딩 감지 신뢰도가 낮아 비대화 모드 규칙대로 latin-1으로 읽었다")
    try:
        return encoding, _convert_to_utf8(path, encoding), True, warnings
    except (UnicodeDecodeError, LookupError):
        warnings.append("감지한 인코딩으로 전체 파일을 읽지 못해 latin-1으로 읽었다")
        return "latin-1", _convert_to_utf8(path, "latin-1"), True, warnings


def _csv_input_error(path: Path, line_num: int | None, exc: csv.Error) -> InputError:
    """v0 러너와 같은 안내: 종류와 위치만(셀 값은 넣지 않는다)."""
    position = f" 마지막으로 읽은 행 번호 {line_num}." if line_num is not None else ""
    return InputError(
        f"입력 CSV를 끝까지 읽지 못했다({path}).{position} "
        f"이유: {type(exc).__name__} — 셀 하나가 너무 크거나"
        "(csv 필드 한도 131072자) 허용되지 않는 바이트가 들어 있다."
    )


def _choose_delimiter(load_path: Path, path: Path) -> str:
    """표본의 필드 수 일관성으로 구분자 후보를 고른다.

    표본은 '줄' 단위로 자르기 때문에 인용 안의 줄바꿈이 끊겨 보인다. 그래서
    한 번도 2칸 이상을 만들지 못하는 후보(모든 줄이 1칸)는 점수를 깎아,
    인용 줄바꿈 때문에 일관성이 흔들린 진짜 구분자가 지지 않게 한다.
    """
    with _open_text(load_path) as source:
        sample = list(itertools.islice(source, DELIMITER_SAMPLE_LINES))
    if not sample:
        return DELIMITER_CANDIDATES[0]
    best: tuple[int, float, int, int] | None = None
    chosen = DELIMITER_CANDIDATES[0]
    for priority, delimiter in enumerate(DELIMITER_CANDIDATES):
        counts: list[int] = []
        try:
            for line in sample:
                counts.append(len(next(csv.reader([line], delimiter=delimiter))))
        except csv.Error as exc:
            raise _csv_input_error(path, None, exc) from exc
        modal_count, hits = Counter(counts).most_common(1)[0]
        score = (1 if modal_count > 1 else 0, hits / len(counts), modal_count, -priority)
        if best is None or score > best:
            best = score
            chosen = delimiter
    return chosen


def _profile_kind(value: str) -> str:
    """프로파일용 셀 형태: empty | int | float | date | text.

    정수와 소수를 나눈다 — 헤더 없음 판정에서 연도형 숫자 헤더('2020')가
    소수 데이터와 다른 세부 형태로 구분되게 한다.
    """
    text = value.strip()
    if not text:
        return "empty"
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        pass
    else:
        return "int" if number.is_integer() else "float"
    if _DATE_RE.match(text):
        return "date"
    return "text"


def _is_missing_marker(value: str) -> bool:
    """흔한 결측 표기인가(대소문자 무시, 앞뒤 공백 제거). 헤더 근거 제외용."""
    return value.strip().lower() in MISSING_MARKERS


def _digit_count(value: str) -> int:
    """숫자 형태 셀의 자릿수(숫자 글자만 센다). 0행 판정의 길이 비교 근거."""
    return sum(1 for ch in value if ch.isascii() and ch.isdigit())


def _column_profiles(rows: list[list[str]]) -> dict[int, str]:
    """데이터 표본으로 컬럼별 형태. 강형(int·float·date 다수)만 돌려준다."""
    if not rows:
        return {}
    width = Counter(len(row) for row in rows).most_common(1)[0][0]
    strong: dict[int, str] = {}
    for index in range(width):
        kinds = [
            kind
            for row in rows
            if len(row) == width
            for kind in [_profile_kind(row[index])]
            if kind != "empty"
        ]
        if kinds:
            majority = Counter(kinds).most_common(1)[0][0]
            if majority in ("int", "float", "date"):
                strong[index] = majority
    return strong


def _digit_range_contains(sample_rows: list[list[str]], index: int, digits: int) -> bool:
    """첫 행 자릿수가 데이터 자릿수 범위 안인가.

    범위는 표본에서 첫 행을 뺀 나머지 전체(앞쪽 작은 값 포함)로 잰다 —
    프로파일 행(검사 구간 뒤)만 쓰면 값이 커지는 순서(일련번호)의 앞쪽
    작은 값이 빠져 첫 행이 범위 밖이 되어버린다(재검토 #122).
    """
    data_digits = [
        _digit_count(row[index])
        for row in sample_rows[1:]
        if index < len(row) and row[index].strip() and not _is_missing_marker(row[index])
    ]
    return bool(data_digits) and min(data_digits) <= digits <= max(data_digits)


def _header_row_count(
    rows: list[list[str]], strong: dict[int, str], sample_rows: list[list[str]]
) -> tuple[int, bool]:
    """위쪽 행이 데이터 형태와 다른 동안 헤더로 센다(최대 HEADER_SCAN_LIMIT행).

    기본은 헤더 1행이다. 낮은 근거로 0행·2행 이상으로 판정하면 데이터 행이
    사라지고 셀 값이 컬럼 이름(로그·LLM 메타데이터로 나가는 값)이 되므로
    판정을 바꾸려면 (1) 강형 컬럼 2개 이상, (2) 값 있는 강형 칸 2개 이상
    (빈 칸과 흔한 결측 표기는 근거로 세지 않는다), (3) 그 값의 텍스트 비율
    0.5 이상이 연속해야 한다. 첫 행이 데이터 형태면 헤더 없음(0행)은 강형
    칸 전부가 데이터의 세부 형태(정수·소수·날짜)와 같고 값 자릿수까지
    데이터 범위(표본에서 첫 행을 뺀 나머지) 안일 때만 인정한다. 자릿수가
    범위 밖인 숫자 강형 컬럼이 **과반**일 때만 헤더 근거로 삼는다 — 한
    컬럼만 어긋나는 것은 일련번호처럼 값이 커지는 순서 데이터다. 연도형
    숫자 헤더('2020')는 소수 데이터와 세부 형태로, 인구 같은 큰 정수
    데이터와는 자릿수로 구분된다.

    Returns:
        (헤더 행 수, 자릿수 근거만으로 1행으로 판정했는가 — 경고용).
    """
    if len(strong) < MIN_STRONG_COLUMNS:
        return 1, False  # 근거가 될 강형 컬럼이 부족하다 — 관례대로 1행 헤더.
    indexes = sorted(strong)
    count = 0
    for row in rows:
        evidence = [
            row[index]
            for index in indexes
            if index < len(row) and row[index].strip() and not _is_missing_marker(row[index])
        ]
        if len(evidence) < MIN_HEADER_EVIDENCE:
            break  # 빈 칸·결측 표기는 헤더 근거가 아니다 — 근거 부족은 데이터로 본다.
        ratio = sum(1 for cell in evidence if _profile_kind(cell) == "text") / len(evidence)
        if ratio < HEADER_TEXT_RATIO:
            break  # 데이터 형태다.
        count += 1
    if count:
        return count, False
    first = rows[0]
    numeric: list[int] = []
    for index in indexes:
        cell = first[index] if index < len(first) else ""
        if not cell.strip() or _is_missing_marker(cell) or _profile_kind(cell) != strong[index]:
            return 1, False  # 세부 형태가 다르거나 판단 재료가 없다 — 기본 1행.
        if strong[index] in ("int", "float"):
            numeric.append(index)  # 날짜는 자릿수가 거의 일정하다 — 형태 검사로 충분하다.
    outside = sum(
        1
        for index in numeric
        if not _digit_range_contains(sample_rows, index, _digit_count(first[index]))
    )
    if numeric and outside * 2 > len(numeric):
        return 1, True  # 자릿수 근거만으로 1행 — 확인 요청 경고를 남긴다.
    return 0, False  # 첫 행까지 데이터와 세부 형태·자릿수가 같다 — 헤더가 없다.


def _normalized_names(header_row: list[str]) -> list[str]:
    return [name.strip() or f"col_{index + 1}" for index, name in enumerate(header_row)]


def _scan_records(
    path: Path, load_path: Path, delimiter: str
) -> tuple[list[list[str]], list[int], Counter[int], int]:
    """한 번의 스트리밍 순회로 표본·줄 위치·폭 분포·총 행 수를 모은다.

    레코드를 저장하는 건 앞부분 표본(SAMPLE_RECORDS개)뿐이라 메모리는 파일
    크기와 무관하다. 빈 물리 행은 데이터가 아니다(DuckDB 적재도 무시한다.
    전 칸이 빈 문자열인 행은 유효한 데이터 행으로 남는다). 표본 레코드와
    함께 그 레코드가 끝나는 물리 줄 번호(csv reader의 line_num — 인용 안
    줄바꿈 포함)를 남겨 skip을 줄 위치로 정한다.
    """
    sample_rows: list[list[str]] = []
    sample_lines: list[int] = []
    widths: Counter[int] = Counter()
    total = 0
    last_line = 0
    try:
        with _open_text(load_path) as source:
            reader = csv.reader(source, delimiter=delimiter)
            for row in reader:
                last_line = reader.line_num
                if not row:
                    continue  # 빈 물리 행은 데이터가 아니다(DuckDB도 무시한다).
                total += 1
                widths[len(row)] += 1
                if len(sample_rows) < SAMPLE_RECORDS:
                    sample_rows.append(row)
                    sample_lines.append(reader.line_num)
    except csv.Error as exc:
        raise _csv_input_error(path, last_line or None, exc) from exc
    return sample_rows, sample_lines, widths, total


def probe_source(path: Path) -> SourceSpec:
    """인코딩·구분자·헤더 행을 추론해 적재와 S0 산출물이 쓸 명세를 만든다.

    오류 경로는 v0 러너와 같다: 빈 CSV와 CSV 구문 오류는 InputError.
    """
    encoding, load_path, converted, warnings = _detect_encoding(path)
    try:
        delimiter = _choose_delimiter(load_path, path)
        if delimiter not in DELIMITER_CANDIDATES:
            raise InputError(f"지원하지 않는 구분자다({path}).")
        sample_rows, sample_lines, widths, total = _scan_records(path, load_path, delimiter)
        if total == 0:
            raise InputError(f"빈 CSV: {path}")
        width, hits = widths.most_common(1)[0]
        if hits != total:
            consistency = hits / total
            warnings.append(f"필드 수가 일관되지 않다(최빈 {width}칸, 일치 비율 {consistency:.3f})")
        # 프로파일은 헤더 검사 구간 뒤의 행으로 한다(검사 구간 행이 프로파일을
        # 오염시키지 않게). 표본이 검사 구간보다 짧으면 첫 행만 빼고 쓴다.
        profile_rows = sample_rows[HEADER_SCAN_LIMIT:] or sample_rows[1:]
        strong = _column_profiles(profile_rows)
        header_rows, numeric_header = _header_row_count(
            sample_rows[:HEADER_SCAN_LIMIT], strong, sample_rows
        )
        if numeric_header:
            # 자릿수 비교만으로 1행을 정했다 — 반대 방향(일련번호 데이터를
            # 헤더로 보는 것)의 위험이 있으니 확인을 요청한다(값은 없다).
            warnings.append(
                "숫자 모양 헤더로 판정했다. 확인이 필요하다 — "
                "첫 행 자릿수가 데이터 자릿수 범위 밖이다"
            )
        applied: list[str] = []
        if header_rows == 0:
            names = [f"col_{index + 1}" for index in range(width)]
            warnings.append("헤더 행이 없다고 판정해 col_N 규칙으로 이름 지었다")
            skip_lines = 0
        else:
            names = _normalized_names(sample_rows[header_rows - 1])
            # 헤더가 끝나는 물리 줄까지 건너뛴다(빈 줄이 앞에 있어도 정확).
            skip_lines = sample_lines[header_rows - 1]
            if header_rows >= 2:
                applied.append(f"다중 행 헤더 {header_rows}행, 마지막 행 사용")
    except BaseException:
        if converted:
            load_path.unlink(missing_ok=True)
        raise
    return SourceSpec(
        original_path=path,
        load_path=load_path,
        encoding=encoding,
        delimiter=delimiter,
        header_row_count=header_rows,
        skip_lines=skip_lines,
        header=names,
        row_count=total - header_rows,
        size_bytes=path.stat().st_size,
        applied_normalizations=applied,
        warnings=warnings,
        converted=converted,
    )


def run(ctx: AnalysisContext, spec: SourceSpec) -> IngestManifest:
    """S0 수집·정규화 산출물. 감지 결과를 그대로 기록한다."""
    # 이름 중복은 #N 규칙(T-0014)으로 유일하게 만든 뒤 모든 산출물이 참조한다.
    sources = deduplicate_source_names(spec.header)
    return IngestManifest(
        files=[
            SourceFile(
                path=spec.original_path.name,
                format=IngestFormat.CSV,
                encoding=spec.encoding,
                size_bytes=spec.size_bytes,
            )
        ],
        columns=[SourceColumn(name=name, index=i) for i, name in enumerate(sources)],
        row_count=spec.row_count,
        delimiter=spec.delimiter,
        header_row_count=spec.header_row_count,
        applied_normalizations=spec.applied_normalizations,
        warnings=spec.warnings,
    )


def source_names(ingest: IngestManifest) -> list[str]:
    """S0 산출물에서 소스 이름 목록을 꺼낸다(이후 단계가 쓴다)."""
    return [column.name for column in ingest.columns]


__all__ = [
    "SourceSpec",
    "deduplicate_source_names",
    "probe_source",
    "run",
    "sanitize_names",
    "source_names",
]
