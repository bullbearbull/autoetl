"""core 내부 예외."""

from __future__ import annotations


class InputError(ValueError):
    """입력 파일 자체의 문제(빈 CSV, CSV 구문 오류).

    안내 메시지에는 셀 값을 담지 않는다(정책 1.2절·금지 패턴 1·6). 경로·행
    번호·컬럼 이름은 로그 L1 이하라 포함해도 된다. pipeline이 재수출한다.
    """
