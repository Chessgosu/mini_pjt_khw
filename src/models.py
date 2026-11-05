"""여러 모듈이 함께 쓰는 자료형과 오류 코드."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Code(str, Enum):
    """오류·안내 코드 전체 목록 (docs/service-spec.md 6.1, 7.2)."""

    # 파일(xlsx) — 검증만 막음
    FILENAME_FORMAT = "FILENAME_FORMAT"
    INVALID_YEAR_MONTH = "INVALID_YEAR_MONTH"
    FILE_UNREADABLE = "FILE_UNREADABLE"
    UNSAFE_CONTENT = "UNSAFE_CONTENT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    DATE_COLUMN_MISMATCH = "DATE_COLUMN_MISMATCH"
    INVALID_CELL_VALUE = "INVALID_CELL_VALUE"
    DUPLICATE_JOB_ID = "DUPLICATE_JOB_ID"
    EMPTY_JOB_ID = "EMPTY_JOB_ID"
    # MD·계산 — 매트릭스와 검증 모두 막음
    MD_FILE_MISSING = "MD_FILE_MISSING"
    MD_SYNTAX_ERROR = "MD_SYNTAX_ERROR"
    START_TIME_INVALID = "START_TIME_INVALID"
    CALENDAR_MISSING = "CALENDAR_MISSING"
    # 확정 — 검증 전체, 매트릭스는 해당 JOB 행만
    BUSINESS_MATCH_UNCONFIRMED = "BUSINESS_MATCH_UNCONFIRMED"
    BASE_DATE_UNCONFIRMED = "BASE_DATE_UNCONFIRMED"
    JOB_RULE_UNCONFIRMED = "JOB_RULE_UNCONFIRMED"
    CONFIRM_SAVE_FAILED = "CONFIRM_SAVE_FAILED"
    # 경고
    JOB_ONLY_IN_FILE = "JOB_ONLY_IN_FILE"
    JOB_ONLY_IN_RULES = "JOB_ONLY_IN_RULES"
    # 채팅 전용
    PII_MASKED = "PII_MASKED"
    OUT_OF_RANGE_MONTH = "OUT_OF_RANGE_MONTH"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    RATE_LIMITED = "RATE_LIMITED"


class Scope(str, Enum):
    """오류가 막는 범위."""

    VERIFY = "verify"
    MATRIX_AND_VERIFY = "matrix_and_verify"
    JOB_ROW = "job_row"
    SINGLE_SAVE = "single_save"
    CHAT_REQUEST = "chat_request"
    NONE = "none"


SCOPE_OF: dict[Code, Scope] = {
    **{c: Scope.VERIFY for c in (
        Code.FILENAME_FORMAT, Code.INVALID_YEAR_MONTH, Code.FILE_UNREADABLE, Code.UNSAFE_CONTENT,
        Code.FILE_TOO_LARGE, Code.DATE_COLUMN_MISMATCH, Code.INVALID_CELL_VALUE,
        Code.DUPLICATE_JOB_ID, Code.EMPTY_JOB_ID)},
    **{c: Scope.MATRIX_AND_VERIFY for c in (
        Code.MD_FILE_MISSING, Code.MD_SYNTAX_ERROR, Code.START_TIME_INVALID, Code.CALENDAR_MISSING)},
    **{c: Scope.JOB_ROW for c in (
        Code.BUSINESS_MATCH_UNCONFIRMED, Code.BASE_DATE_UNCONFIRMED, Code.JOB_RULE_UNCONFIRMED)},
    Code.CONFIRM_SAVE_FAILED: Scope.SINGLE_SAVE,
    Code.JOB_ONLY_IN_FILE: Scope.NONE,
    Code.JOB_ONLY_IN_RULES: Scope.NONE,
    **{c: Scope.CHAT_REQUEST for c in (
        Code.PII_MASKED, Code.OUT_OF_RANGE_MONTH, Code.INPUT_TOO_LONG, Code.RATE_LIMITED)},
}

WARN_CODES = {Code.JOB_ONLY_IN_FILE, Code.JOB_ONLY_IN_RULES, Code.PII_MASKED}


@dataclass(frozen=True)
class Finding:
    """가드레일 결과 한 건 (ontology: GuardrailFinding)."""

    code: Code
    message: str
    job_id: str | None = None
    missing_month: str | None = None
    file: str | None = None

    @property
    def scope(self) -> Scope:
        return SCOPE_OF[self.code]

    @property
    def severity(self) -> str:
        return "warn" if self.code in WARN_CODES else "stop"
