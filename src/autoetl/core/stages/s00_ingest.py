"""S0 수집·정규화 — 입력 추론(probe_source)과 IngestManifest.

추론 규칙(리드 결정, T-0023):

- 인코딩: BOM 확인 → UTF-8 엄격 디코딩 → 실패하면 charset-normalizer 감지
  (CP949·EUC-KR 주의). 신뢰도가 낮으면 경고를 남기고 비대화 모드 SOP대로
  latin-1으로 다시 읽는다. UTF-8이 아니면 임시 UTF-8 파일로 변환해 적재하고
  컨텍스트가 실행 끝(예외 포함)에 지운다.
- 구분자: 앞부분 표본에서 후보(쉼표·세미콜론·탭·파이프)를 골라 전체 파일의
  필드 수 일관성으로 확정한다. 인용 필드 안의 구분자는 csv 모듈이 처리한다.
- 헤더 행 수: 위쪽 최대 5행을 데이터 형태 프로파일(숫자·날짜·텍스트 종류)과
  비교한다. 숫자·날짜 컬럼(강형 컬럼)의 값이 텍스트 형태인 행은 헤더로
  판정하고, 연속한 동안 헤더로 센다. 다중 행 헤더면 컬럼 이름은 마지막 헤더
  행(필드 행)이고 applied_normalizations에 남긴다. 헤더가 없다고 판정하면
  col_1..N으로 이름 짓고 경고를 남긴다(SOP).
- 이름 중복은 T-0014 규칙(#N 접미)을 유지한다.

v0 한계: 인코딩 판정을 위해 파일 전체를 한 번 읽는다(벤치 규모 기준). 대용량
스트리밍 판정은 이후 단계에서 최적화한다.
"""

from __future__ import annotations

import csv
import io
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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
HEADER_TEXT_RATIO = 0.5
ENCODING_SAMPLE_BYTES = 64 * 1024
LOW_CONFIDENCE = 0.7

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


def _cell_kind(value: str) -> str:
    """셀 하나의 형태 종류: empty | number | date | text."""
    text = value.strip()
    if not text:
        return "empty"
    try:
        float(text.replace(",", ""))
    except ValueError:
        pass
    else:
        return "number"
    if _DATE_RE.match(text):
        return "date"
    return "text"


def _write_utf8_temp(text: str) -> Path:
    """변환된 UTF-8 임시 파일. 실패해도 남지 않게 probe가 관리한다.

    newline=""로 써서 줄바꿈을 그대로 둔다 — Windows 텍스트 모드 기본값은
    '\\n'을 '\\r\\n'으로 바꿔 원본의 '\\r\\n'이 '\\r\\r\\n'이 되고 적재가 깨진다.
    """
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - 경로를 넘겨야 해서 직접 닫는다
        "w", encoding="utf-8", newline="", suffix=".autoetl-utf8.csv", delete=False
    )
    try:
        handle.write(text)
        handle.close()
    except BaseException:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise
    return Path(handle.name)


def _detect_encoding(path: Path) -> tuple[str, Path, bool, list[str]]:
    """인코딩을 감지해 (encoding, load_path, converted, warnings)을 돌려준다."""
    raw = path.read_bytes()
    warnings: list[str] = []
    for bom, encoding in _BOM_ENCODINGS:
        if raw.startswith(bom):
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                warnings.append(f"BOM이 {encoding}을(를) 가리키지만 엄격 디코딩에 실패했다")
                break
            if encoding == "utf-8-sig":
                # UTF-8 BOM: 원본을 그대로 쓴다(헤더 행은 skip으로 건너뛴다).
                return "utf-8", path, False, warnings
            return encoding, _write_utf8_temp(text), True, warnings
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return "utf-8", path, False, warnings

    match = from_bytes(raw[:ENCODING_SAMPLE_BYTES]).best()
    encoding = "latin-1"  # SOP(비대화 모드): latin-1은 어떤 바이트든 읽는다.
    # charset-normalizer는 정돈된 짧은 텍스트에서 coherence가 0으로 나올 수
    # 있다(벤치 cp949가 그렇다). 순위는 best()가 이미 정했으니 신뢰도는
    # coherence와 chaos(낮을수록 좋음)를 합쳐 잰다.
    confidence = 0.0 if match is None else max(match.coherence, 1.0 - match.chaos)
    if match is None or confidence < LOW_CONFIDENCE:
        warnings.append("인코딩 감지 신뢰도가 낮아 비대화 모드 규칙대로 latin-1으로 읽었다")
    else:
        encoding = match.encoding
    try:
        text = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        warnings.append("감지한 인코딩으로 전체 파일을 읽지 못해 latin-1으로 읽었다")
        text = raw.decode("latin-1")
        encoding = "latin-1"
    return encoding, _write_utf8_temp(text), True, warnings


def _csv_input_error(path: Path, line_num: int | None, exc: csv.Error) -> InputError:
    """v0 러너와 같은 안내: 종류와 위치만(셀 값은 넣지 않는다)."""
    position = f" 마지막으로 읽은 행 번호 {line_num}." if line_num is not None else ""
    return InputError(
        f"입력 CSV를 끝까지 읽지 못했다({path}).{position} "
        f"이유: {type(exc).__name__} — 셀 하나가 너무 크거나"
        "(csv 필드 한도 131072자) 허용되지 않는 바이트가 들어 있다."
    )


def _choose_delimiter(text: str, path: Path) -> str:
    """표본의 필드 수 일관성으로 구분자 후보를 고른다.

    표본은 '줄' 단위로 자르기 때문에 인용 안의 줄바꿈이 끊겨 보인다. 그래서
    한 번도 2칸 이상을 만들지 못하는 후보(모든 줄이 1칸)는 점수를 깎아,
    인용 줄바꿈 때문에 일관성이 흔들린 진짜 구분자가 지지 않게 한다.
    """
    sample = text.splitlines()[:DELIMITER_SAMPLE_LINES]
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


def _records(text: str, delimiter: str, path: Path) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        return list(reader)
    except csv.Error as exc:
        raise _csv_input_error(path, reader.line_num, exc) from exc


def _column_profiles(records: list[list[str]]) -> dict[int, str]:
    """아래쪽 표본(데이터)로 컬럼별 형태 프로파일. 강형(number·date)만 돌려준다."""
    data_rows = [row for row in records[-PROFILE_SAMPLE_ROWS:] if row]
    if not data_rows:
        return {}
    width = Counter(len(row) for row in data_rows).most_common(1)[0][0]
    strong: dict[int, str] = {}
    for index in range(width):
        kinds = [
            kind
            for row in data_rows
            if len(row) == width
            for kind in [_cell_kind(row[index])]
            if kind != "empty"
        ]
        if kinds:
            majority = Counter(kinds).most_common(1)[0][0]
            if majority in ("number", "date"):
                strong[index] = majority
    return strong


def _header_row_count(records: list[list[str]]) -> int:
    """위쪽 행이 데이터 형태와 다른 동안 헤더로 센다(최대 HEADER_SCAN_LIMIT행)."""
    strong = _column_profiles(records)
    if not strong:
        return 1  # 강형 컬럼이 없으면 형태 비교가 무의미 — 관례대로 1행 헤더.
    indexes = sorted(strong)
    for offset, row in enumerate(records[:HEADER_SCAN_LIMIT]):
        cells = [row[index] if index < len(row) else "" for index in indexes]
        text_ratio = sum(1 for cell in cells if _cell_kind(cell) in ("text", "empty")) / len(cells)
        if text_ratio < HEADER_TEXT_RATIO:
            return offset  # 첫 데이터 행 직전까지가 헤더다.
    return min(HEADER_SCAN_LIMIT, len(records))


def _normalized_names(header_row: list[str]) -> list[str]:
    return [name.strip() or f"col_{index + 1}" for index, name in enumerate(header_row)]


def probe_source(path: Path) -> SourceSpec:
    """인코딩·구분자·헤더 행을 추론해 적재와 S0 산출물이 쓸 명세를 만든다.

    오류 경로는 v0 러너와 같다: 빈 CSV와 CSV 구문 오류는 InputError.
    """
    encoding, load_path, converted, warnings = _detect_encoding(path)
    try:
        # utf-8-sig로 읽어 BOM이 있으면 뗀다(없으면 그대로). 변환 임시 파일에는
        # BOM이 없다.
        text = load_path.read_text(encoding="utf-8-sig")
        if not text.strip():
            raise InputError(f"빈 CSV: {path}")
        delimiter = _choose_delimiter(text, path)
        if delimiter not in DELIMITER_CANDIDATES:
            raise InputError(f"지원하지 않는 구분자다({path}).")
        records = _records(text, delimiter, path)
        records = [row for row in records if row]  # 빈 행은 데이터가 아니다.
        width = Counter(len(row) for row in records).most_common(1)[0][0]
        consistent = sum(1 for row in records if len(row) == width) / len(records)
        if consistent < 1.0:
            warnings.append(f"필드 수가 일관되지 않다(최빈 {width}칸, 일치 비율 {consistent:.3f})")
        header_rows = _header_row_count(records)
        header_records = records[:header_rows]
        data_rows = records[header_rows:]
        if header_rows == 0:
            names = [f"col_{index + 1}" for index in range(width)]
            warnings.append("헤더 행이 없다고 판정해 col_N 규칙으로 이름 지었다")
        else:
            names = _normalized_names(header_records[-1])
        applied: list[str] = []
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
        header=names,
        row_count=len(data_rows),
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
