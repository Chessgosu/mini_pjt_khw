"""02단계 ac_engine_correct: 규칙 문법, D 연산, 등록일 규칙, 월 기준 규칙, 달력 범위.

D 연산은 가상 달력(2026-09 공휴일 없음)과 가상 업무 "정산"으로 검증한다.
"""
from datetime import date
from pathlib import Path

import pytest

from src.engine import (
    NORMALIZE, REJECT, STANDARD, BaseDates, BizDays, Confirmed, classify_rule, compute_schedule,
    month_rule_dates, parse_base_date_rule, parse_d_op, registration_date,
)
from src.models import Code
from src.parsing import load_inputs
from tests.helpers import fixture_calendar, make_inputs, sample_confirmed

D = date

# ------------------------------------------------------------ 문법 판정 (seed의 판정 예시)

@pytest.mark.parametrize("rule", [
    "매월 25일", "말일", "첫 영업일", "마지막 영업일", "2째 주 월요일", "5째 주 금요일", "매일", "매 영업일",
    "자동이체 5일자 응당일 D일", "자동이체 5,15,25일자 응당일 D+1일", "매 자동이체 응당일 D-1 영업일",
    "마감일 D+2 영업일", "정산 기준일 D일 + 1영업일", "정산 기준일 영업일 D일 + 1일", "정산 기준일 D영업일",
])
def test_standard_rules(rule: str) -> None:
    assert classify_rule(rule).status == STANDARD


@pytest.mark.parametrize("rule", [
    "매월 첫째주 토요일", "매월 둘째주 토요일", "매월 첫 영업일 D일", "매월 마감일 D+2 일",
    "매월 마감일 D+2 영업일", "자동이체 5,15,25일자 응당일 D일 + 1일", "",
])
def test_normalize_rules(rule: str) -> None:
    assert classify_rule(rule).status == NORMALIZE


@pytest.mark.parametrize("rule", ["매월 32일", "매월 0일", "6째 주 월요일", "D+1 영업일", "D영업일"])
def test_rejected_rules(rule: str) -> None:
    assert classify_rule(rule).status == REJECT


def test_rejected_rule_blocks_matrix(fixtures_dir: Path) -> None:
    inputs = make_inputs("| Z001 | x | 매월 32일 | 10:00 | |\n", "## 정산\n매월 20일\n",
                         fixture_calendar("calendar-2026-09-virtual.md"))
    result = compute_schedule(inputs, Confirmed(), "2026-09")
    assert result.blocked and [f.code for f in result.findings] == [Code.MD_SYNTAX_ERROR]


# ------------------------------------------------------------ D 연산 (가상 업무 정산)

@pytest.mark.parametrize("start,op,expected", [
    # D = 2026-09-20 (일요일, 휴일)
    ("매월 20일", "D+1일", D(2026, 9, 21)),
    ("매월 20일", "D영업일", D(2026, 9, 21)),
    ("매월 20일", "D+1 영업일", D(2026, 9, 22)),
    ("매월 20일", "D일 + 1영업일", D(2026, 9, 21)),
    ("매월 20일", "영업일 D일 + 1일", D(2026, 9, 22)),
    ("매월 20일", "D-1 영업일", D(2026, 9, 18)),
    ("매월 20일", "D일", D(2026, 9, 20)),
    ("매월 20일", "D-1일", D(2026, 9, 19)),
    # D = 2026-09-21 (월요일, 영업일)
    ("매월 21일", "D영업일", D(2026, 9, 21)),
    ("매월 21일", "D+1 영업일", D(2026, 9, 22)),
    ("매월 21일", "D일 + 1영업일", D(2026, 9, 22)),
    ("매월 21일", "영업일 D일 + 1일", D(2026, 9, 22)),
    ("매월 21일", "D-1 영업일", D(2026, 9, 18)),
])
def test_d_operators(start: str, op: str, expected: date) -> None:
    bd = BizDays(fixture_calendar("calendar-2026-09-virtual.md"))
    base = BaseDates({"정산 기준일": parse_base_date_rule("정산 기준일", start)}, bd)
    occ = base.occurrence("정산 기준일", "2026-09")
    assert parse_d_op(op).apply(occ.date, bd) == expected


def test_d_operator_through_compute() -> None:
    """같은 사례를 compute_schedule 경로로도 확인 (21:00 시작 → 등록일 = 수행일)."""
    rows = "".join(f"| S{i} | x | 정산 기준일 {op} | 21:00 | |\n" for i, op in
                   enumerate(["D+1일", "D영업일", "D+1 영업일", "D일 + 1영업일", "영업일 D일 + 1일", "D-1 영업일"]))
    inputs = make_inputs(rows, "## 정산\n정산 기준일은 매월 20일\n", fixture_calendar("calendar-2026-09-virtual.md"))
    result = compute_schedule(inputs, Confirmed(base_date_rules={"정산 기준일": "매월 20일"}), "2026-09")
    assert not result.blocked, result.findings
    got = [result.dates_of(f"S{i}") for i in range(6)]
    assert got == [[D(2026, 9, 21)], [D(2026, 9, 21)], [D(2026, 9, 22)], [D(2026, 9, 21)], [D(2026, 9, 22)],
                   [D(2026, 9, 18)]]


# ------------------------------------------------------------ 등록일 규칙

@pytest.mark.parametrize("run,start,expected", [
    (D(2026, 11, 20), "21:00", D(2026, 11, 20)),
    (D(2026, 11, 20), "02:00", D(2026, 11, 19)),
    (D(2026, 11, 20), "16:29", D(2026, 11, 19)),
    (D(2026, 11, 20), "16:30", D(2026, 11, 20)),
    (D(2026, 11, 20), "00:00", D(2026, 11, 19)),
    (D(2026, 11, 20), "23:59", D(2026, 11, 20)),
    (D(2026, 11, 23), "02:00", D(2026, 11, 22)),     # 월요일 → 일요일 등록
    (D(2026, 12, 1), "09:00", D(2026, 11, 30)),      # 다음 달 수행 → 앞 달 등록
    (D(2027, 1, 1), "01:00", D(2026, 12, 31)),       # 연말 경계
])
def test_registration_rule(run: date, start: str, expected: date) -> None:
    assert registration_date(run, start) == expected


# ------------------------------------------------------------ 월 기준 규칙 (실제 2026-11 달력)

@pytest.fixture
def bd_real(data_dir: Path) -> BizDays:
    return BizDays(load_inputs(data_dir).calendar)


def test_month_rules_2026_11(bd_real: BizDays) -> None:
    ym = "2026-11"
    assert month_rule_dates("5째 주 금요일", ym, bd_real) == []          # 금요일 6·13·20·27일뿐
    assert month_rule_dates("2째 주 월요일", ym, bd_real) == [D(2026, 11, 9)]
    assert month_rule_dates("1째 주 토요일", ym, bd_real) == [D(2026, 11, 7)]
    assert len(month_rule_dates("매일", ym, bd_real)) == 30
    assert len(month_rule_dates("매 영업일", ym, bd_real)) == 21
    assert month_rule_dates("첫 영업일", ym, bd_real) == [D(2026, 11, 2)]
    assert month_rule_dates("마지막 영업일", ym, bd_real) == [D(2026, 11, 30)]
    assert month_rule_dates("매월 31일", ym, bd_real) == [D(2026, 11, 30)]   # 없는 날짜 → 말일
    assert month_rule_dates("말일", ym, bd_real) == [D(2026, 11, 30)]


def test_holiday_is_not_business_day(bd_real: BizDays) -> None:
    assert not bd_real.is_bd(D(2026, 12, 25))           # 성탄절(금)
    assert bd_real.next_bd(D(2026, 12, 25)) == D(2026, 12, 28)
    assert bd_real.next_bd(D(2026, 10, 3)) == D(2026, 10, 5)


# ------------------------------------------------------------ 달력 범위

@pytest.mark.parametrize("ym,missing", [("2026-10", "2026-09"), ("2027-01", "2027-02")])
def test_calendar_missing_outside_supported_months(data_dir: Path, ym: str, missing: str) -> None:
    result = compute_schedule(load_inputs(data_dir), sample_confirmed(), ym)
    assert result.blocked
    assert [(f.code, f.missing_month) for f in result.findings] == [(Code.CALENDAR_MISSING, missing)]


def test_calendar_missing_when_next_month_removed(data_dir: Path) -> None:
    path = data_dir / "business-calendar.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.split("## 2026-12")[0] + "## 2027-01" + text.split("## 2027-01")[1], encoding="utf-8")
    result = compute_schedule(load_inputs(data_dir), sample_confirmed(), "2026-11")
    assert [(f.code, f.missing_month) for f in result.findings] == [(Code.CALENDAR_MISSING, "2026-12")]


@pytest.mark.parametrize("ym", ["2026-11", "2026-12"])
def test_supported_months_compute(data_dir: Path, ym: str) -> None:
    result = compute_schedule(load_inputs(data_dir), sample_confirmed(), ym)
    assert not result.blocked, result.findings
    assert result.unconfirmed == {}
    assert result.entries


# ------------------------------------------------------------ 규칙 우선순위, 미확정

def test_rule_priority_over_job_name(data_dir: Path) -> None:
    """A001 '매출 마감'은 이름과 달리 규칙(첫 영업일)대로 11-02 수행 → 11-01 등록."""
    result = compute_schedule(load_inputs(data_dir), sample_confirmed(), "2026-11")
    assert result.dates_of("A001") == [D(2026, 11, 1), D(2026, 11, 30)]


def test_unconfirmed_jobs_are_marked_not_computed(data_dir: Path) -> None:
    result = compute_schedule(load_inputs(data_dir), Confirmed(), "2026-11")
    assert not result.blocked
    assert result.unconfirmed["A001"] == [Code.JOB_RULE_UNCONFIRMED]          # 정규화 대상
    assert result.unconfirmed["B001"] == [Code.BASE_DATE_UNCONFIRMED]          # 기준일 규칙 없음
    assert "D001" not in result.unconfirmed and result.dates_of("D001")        # 표준 월 규칙은 바로 계산
    assert result.dates_of("B001") == []


def test_business_match_unconfirmed(data_dir: Path) -> None:
    confirmed = sample_confirmed()
    confirmed.business_matches = {}
    result = compute_schedule(load_inputs(data_dir), confirmed, "2026-11")
    assert result.unconfirmed["A002"] == [Code.BUSINESS_MATCH_UNCONFIRMED]   # "마감일" ≠ "## 마감"
    assert "B001" not in result.unconfirmed                                    # "자동이체"는 제목과 같음


def test_circular_base_date_reference(data_dir: Path) -> None:
    confirmed = sample_confirmed()
    confirmed.base_date_rules["마감일"] = "결산 기준일과 같음"
    confirmed.base_date_rules["결산 기준일"] = "마감일과 같음"
    result = compute_schedule(load_inputs(data_dir), confirmed, "2026-11")
    assert result.blocked and all(f.code == Code.MD_SYNTAX_ERROR for f in result.findings)


def test_explanation_required_when_rules_use_base_dates(data_dir: Path) -> None:
    (data_dir / "business-explanation.md").unlink()
    result = compute_schedule(load_inputs(data_dir), sample_confirmed(), "2026-11")
    assert result.blocked and result.findings[0].code == Code.MD_FILE_MISSING


def test_deterministic(data_dir: Path) -> None:
    a = compute_schedule(load_inputs(data_dir), sample_confirmed(), "2026-11")
    b = compute_schedule(load_inputs(data_dir), sample_confirmed(), "2026-11")
    assert a.entries == b.entries
