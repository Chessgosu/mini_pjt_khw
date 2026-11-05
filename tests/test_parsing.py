"""01단계: 입력 MD 3종 파싱과 MD 오류 코드."""
from datetime import date
from pathlib import Path

import pytest

from src.models import Code, Scope
from src.parsing import (
    load_inputs, parse_calendar, parse_explanation, parse_schedule, split_rule, text_hash,
)

HEADER = "| JOB ID | JOB 이름 | 수행일 규칙 | 시작 시각 | JOB 설명 |\n|---|---|---|---|---|\n"


def codes(findings):
    return [f.code for f in findings]


# ------------------------------------------------------------ 실제 data/

def test_real_data_counts(data_dir: Path) -> None:
    inputs = load_inputs(data_dir)
    assert inputs.findings == []
    assert len(inputs.jobs) == 22
    assert inputs.explanation is not None
    assert inputs.explanation.titles == ["자동이체", "급여", "마감", "결산"]
    assert sorted(inputs.calendar.months) == ["2026-10", "2026-11", "2026-12", "2027-01"]


def test_real_data_values(data_dir: Path) -> None:
    inputs = load_inputs(data_dir)
    b202 = inputs.job("B202")        # 원본이 "| B202|"처럼 공백 없이 적혀 있음
    assert b202 is not None and b202.rule == "자동이체 10,20,말일자 응당일 D일 + 1일"
    assert inputs.job("A002").start_time == "16:30"
    assert inputs.job("D001").rule == "매일"
    cal = inputs.calendar
    assert cal.is_holiday(date(2026, 12, 25)) and cal.is_holiday(date(2026, 10, 3))
    assert not cal.is_holiday(date(2026, 11, 20))
    assert cal.months["2026-11"].holidays == {}


def test_files_reread_every_call(data_dir: Path) -> None:
    assert len(load_inputs(data_dir).jobs) == 22
    path = data_dir / "batch-schedule.md"
    path.write_text(path.read_text(encoding="utf-8") + "| Z001 | 추가 | 매일 | 22:00 | |\n", encoding="utf-8")
    assert len(load_inputs(data_dir).jobs) == 23


# ------------------------------------------------------------ MD_FILE_MISSING

@pytest.mark.parametrize("name", ["batch-schedule.md", "business-calendar.md"])
def test_required_file_missing(data_dir: Path, name: str) -> None:
    (data_dir / name).unlink()
    f = load_inputs(data_dir).findings
    assert codes(f) == [Code.MD_FILE_MISSING]
    assert f[0].file == name and f[0].scope == Scope.MATRIX_AND_VERIFY


@pytest.mark.parametrize("name", ["batch-schedule.md", "business-calendar.md"])
def test_required_file_empty(data_dir: Path, name: str) -> None:
    (data_dir / name).write_text("  \n\n", encoding="utf-8")
    assert codes(load_inputs(data_dir).findings) == [Code.MD_FILE_MISSING]


def test_explanation_missing_is_deferred(data_dir: Path) -> None:
    """business-explanation.md는 필요할 때만 필수 → 파싱 단계에서는 오류 없이 None."""
    (data_dir / "business-explanation.md").unlink()
    inputs = load_inputs(data_dir)
    assert inputs.explanation is None and inputs.findings == []


# ------------------------------------------------------------ MD_SYNTAX_ERROR

def test_missing_required_column() -> None:
    _, f = parse_schedule("| JOB ID | JOB 이름 | 시작 시각 |\n|---|---|---|\n| A001 | x | 02:00 |\n")
    assert codes(f) == [Code.MD_SYNTAX_ERROR] and "수행일 규칙" in f[0].message


def test_duplicate_and_empty_job_id() -> None:
    jobs, f = parse_schedule(HEADER + "| A001 | a | 매일 | 02:00 | |\n| A001 | b | 매일 | 03:00 | |\n| | c | 매일 | 04:00 | |\n")
    assert [j.job_id for j in jobs] == ["A001"]
    assert codes(f) == [Code.MD_SYNTAX_ERROR, Code.MD_SYNTAX_ERROR]
    assert f[0].job_id == "A001"


def test_calendar_bad_heading_and_date() -> None:
    _, f = parse_calendar("## 2026-13\n\n## 2026-11\n| 날짜 | 설명 |\n|---|---|\n| 2026-11-31 | x |\n| 2026-12-01 | y |\n")
    assert codes(f) == [Code.MD_SYNTAX_ERROR] * 3


def test_explanation_without_sections() -> None:
    _, f = parse_explanation("# 업무 기준 설명\n\n본문만 있음\n")
    assert codes(f) == [Code.MD_SYNTAX_ERROR]


# ------------------------------------------------------------ START_TIME_INVALID

@pytest.mark.parametrize("value,valid", [
    ("00:00", True), ("16:29", True), ("16:30", True), ("23:59", True),
    ("24:00", False), ("9:05", False), ("", False), ("12:60", False), ("12-30", False),
])
def test_start_time_format(value: str, valid: bool) -> None:
    jobs, f = parse_schedule(HEADER + f"| A001 | a | 매일 | {value} | |\n")
    assert (jobs[0].start_time == value) is valid
    assert codes(f) == ([] if valid else [Code.START_TIME_INVALID])


# ------------------------------------------------------------ 해시

def test_hash_ignores_whitespace() -> None:
    assert text_hash("약정일은  매월 20일\n입니다. ") == text_hash("약정일은 매월 20일 입니다.")
    assert text_hash("매월 20일") != text_hash("매월 21일")


def test_section_and_title_hashes() -> None:
    base = "## 자동이체\n출금일 5일\n\n## 급여\n21일\n"
    e1, _ = parse_explanation(base)
    e2, _ = parse_explanation(base.replace("21일", "25일"))
    assert e1.sections["자동이체"].text_hash == e2.sections["자동이체"].text_hash   # 다른 절만 바뀜
    assert e1.sections["급여"].text_hash != e2.sections["급여"].text_hash
    assert e1.titles_hash == e2.titles_hash
    e3, _ = parse_explanation(base + "\n## 결산\n마감일과 같음\n")
    assert e1.titles_hash != e3.titles_hash                                         # 제목 추가


# ------------------------------------------------------------ 규칙 문자열 나누기

@pytest.mark.parametrize("rule,base,d_op,every,expr", [
    ("매월 첫 영업일 D일", "매월 첫 영업일", "D일", False, "첫 영업일"),
    ("매월 마감일 D+2 영업일", "매월 마감일", "D+2 영업일", False, "마감일"),
    ("매월 마감일 D+2 일", "매월 마감일", "D+2 일", False, "마감일"),
    ("자동이체 5일자 응당일 D일", "자동이체 5일자 응당일", "D일", False, "자동이체 5일자 응당일"),
    ("자동이체 10일자 응당일 D+1 영업일", "자동이체 10일자 응당일", "D+1 영업일", False, "자동이체 10일자 응당일"),
    ("자동이체 5,15,25일자 응당일 D일 + 1일", "자동이체 5,15,25일자 응당일", "D일 + 1일", False, "자동이체 5,15,25일자 응당일"),
    ("매 자동이체 응당일 D-1 영업일", "매 자동이체 응당일", "D-1 영업일", True, "자동이체 응당일"),
    ("정산 기준일 영업일 D일 + 1일", "정산 기준일", "영업일 D일 + 1일", False, "정산 기준일"),
    ("정산 기준일 D일 + 1영업일", "정산 기준일", "D일 + 1영업일", False, "정산 기준일"),
    ("정산 기준일 D영업일", "정산 기준일", "D영업일", False, "정산 기준일"),
    ("매월 첫째주 토요일", None, None, False, None),
    ("매일", None, None, False, None),
    ("매 영업일", None, None, False, None),
    ("말일", None, None, False, None),
])
def test_split_rule(rule, base, d_op, every, expr) -> None:
    p = split_rule(rule)
    assert (p.base_text, p.d_op, p.every, p.business_expr) == (base, d_op, every, expr)


def test_split_rule_edge_cases() -> None:
    assert split_rule("") is None and split_rule("   ") is None
    only_op = split_rule("D+1 영업일")          # 업무 표현 없음 → 02단계에서 MD_SYNTAX_ERROR
    assert only_op.d_op == "D+1 영업일" and only_op.business_expr is None and not only_op.is_month_rule


def test_all_real_rules_split(data_dir: Path) -> None:
    for job in load_inputs(data_dir).jobs:
        parts = split_rule(job.rule)
        assert parts is not None, job.job_id
        if parts.d_op:
            assert parts.business_expr, job.job_id
