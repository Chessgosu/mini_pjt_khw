"""04단계 ac_guardrail_codes: 오류 코드별 범위·심각도, 검증 단계에서 나오는 코드의 범위가 명세와 같은지."""
from pathlib import Path

import pytest

from src.models import SCOPE_OF, Code, Finding, Scope
from src.verify import verify_upload
from tests.helpers import sample_confirmed

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

FILE_CODES = {Code.FILENAME_FORMAT, Code.INVALID_YEAR_MONTH, Code.FILE_UNREADABLE, Code.UNSAFE_CONTENT,
              Code.FILE_TOO_LARGE, Code.DATE_COLUMN_MISMATCH, Code.INVALID_CELL_VALUE,
              Code.DUPLICATE_JOB_ID, Code.EMPTY_JOB_ID}


def test_every_code_has_scope() -> None:
    assert set(SCOPE_OF) == set(Code)


@pytest.mark.parametrize("code", sorted(FILE_CODES, key=lambda c: c.value))
def test_file_codes_block_verify_only(code: Code) -> None:
    f = Finding(code, "x")
    assert f.scope == Scope.VERIFY and f.severity == "stop"


@pytest.mark.parametrize("code", [Code.JOB_ONLY_IN_FILE, Code.JOB_ONLY_IN_RULES])
def test_job_mismatch_is_warning(code: Code) -> None:
    f = Finding(code, "x")
    assert f.scope == Scope.NONE and f.severity == "warn"


@pytest.mark.parametrize("code", [Code.MD_FILE_MISSING, Code.MD_SYNTAX_ERROR, Code.START_TIME_INVALID,
                                  Code.CALENDAR_MISSING])
def test_md_codes_block_matrix_and_verify(code: Code) -> None:
    assert Finding(code, "x").scope == Scope.MATRIX_AND_VERIFY


@pytest.mark.parametrize("code", [Code.BUSINESS_MATCH_UNCONFIRMED, Code.BASE_DATE_UNCONFIRMED,
                                  Code.JOB_RULE_UNCONFIRMED])
def test_unconfirmed_codes_are_job_row_scope(code: Code) -> None:
    assert Finding(code, "x").scope == Scope.JOB_ROW


def test_invalid_file_findings_are_all_verify_scope() -> None:
    """잘못된 파일 10종에서 나온 사유는 모두 검증만 막는 파일 코드다 (매트릭스는 계속 보임)."""
    for path in (SAMPLES / "invalid").glob("*/*.xlsx"):
        from src.verify import validate_upload
        for f in validate_upload(path.read_bytes(), path.name).findings:
            assert f.code in FILE_CODES and f.scope == Scope.VERIFY and f.file == "xlsx", (path, f)


def test_warnings_do_not_block_verdicts(data_dir: Path) -> None:
    path = SAMPLES / "with-errors" / "BATCH_SCHEDULE_202611.xlsx"
    judged = verify_upload(data_dir, path.read_bytes(), path.name, sample_confirmed()).judged
    assert judged.warnings and all(w.severity == "warn" for w in judged.warnings)
    assert not judged.blocked and judged.verdicts


def test_md_error_blocks_verification(data_dir: Path) -> None:
    p = data_dir / "batch-schedule.md"
    p.write_text(p.read_text(encoding="utf-8").replace("| 22:00 |", "| 25:00 |", 1), encoding="utf-8")
    path = SAMPLES / "correct" / "BATCH_SCHEDULE_202611.xlsx"
    judged = verify_upload(data_dir, path.read_bytes(), path.name, sample_confirmed()).judged
    assert judged.blocked and Code.START_TIME_INVALID in {f.code for f in judged.blocked_by}
