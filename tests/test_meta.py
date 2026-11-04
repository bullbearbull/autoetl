"""패키지 기본 메타 검증."""

import autoetl


def test_version_is_semver() -> None:
    parts = autoetl.__version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
