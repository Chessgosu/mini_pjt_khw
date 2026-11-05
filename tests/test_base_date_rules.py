"""02단계 ac_base_date_rules: 기준일 규칙(휴일 보정, 겹침 보정, 겹침 제외, 참조)."""
from datetime import date
from pathlib import Path

import pytest

from src.engine import BaseDates, BizDays, compute_schedule, parse_base_date_rule
from src.parsing import load_inputs
from tests.helpers import fixture_calendar, sample_base_date_rules, sample_confirmed

D = date


def base_dates(calendar) -> BaseDates:
    rules = {n: parse_base_date_rule(n, t) for n, t in sample_base_date_rules().items()}
    return BaseDates(rules, BizDays(calendar))


@pytest.fixture
def real(data_dir: Path):
    return load_inputs(data_dir)


def test_parse_base_date_rule() -> None:
    r = parse_base_date_rule("자동이체 5일자 응당일", "매월 5일, 휴일이면 다음 영업일, 앞선 자동이체 응당일과 겹치면 수행 안 함")
    assert (r.start, r.start_is_ref, r.holiday, r.exclude_group) == ("매월 5일", False, "next", "자동이체 응당일")
    r = parse_base_date_rule("마감일", "첫 영업일, 자동이체 응당일과 같으면 다음 영업일")
    assert (r.start, r.overlap_next) == ("첫 영업일", "자동이체 응당일")
    r = parse_base_date_rule("결산 기준일", "마감일과 같음")
    assert (r.start, r.start_is_ref) == ("마감일", True)


@pytest.mark.parametrize("key,expected", [
    ("5일자", D(2026, 12, 7)),     # 12-05 토 → 월
    ("10일자", D(2026, 12, 10)),
    ("15일자", D(2026, 12, 15)),
    ("20일자", D(2026, 12, 21)),   # 12-20 일 → 월
    ("25일자", D(2026, 12, 28)),   # 12-25 성탄절 → 월
    ("말일자", D(2026, 12, 31)),
])
def test_december_due_dates(real, key: str, expected: date) -> None:
    assert base_dates(real.calendar).occurrence(f"자동이체 {key} 응당일", "2026-12").date == expected


def test_october_month_end_moves_into_november(real) -> None:
    occ = base_dates(real.calendar).occurrence("자동이체 말일자 응당일", "2026-10")
    assert (occ.original, occ.date) == (D(2026, 10, 31), D(2026, 11, 2))


def test_closing_day_skips_due_date(real) -> None:
    """11월 첫 영업일 11-02가 10월 말일자 응당일이므로 마감일은 다음 영업일 11-03."""
    bdates = base_dates(real.calendar)
    assert bdates.occurrence("마감일", "2026-11").date == D(2026, 11, 3)
    assert bdates.occurrence("마감일", "2026-12").date == D(2026, 12, 1)


def test_b201_december_runs(real) -> None:
    result = compute_schedule(real, sample_confirmed(), "2026-12")
    runs = sorted({e.run_date for e in result.entries if e.job_id == "B201"})
    assert [r for r in runs if r.month == 12] == [D(2026, 12, 8), D(2026, 12, 16), D(2026, 12, 29)]


def test_b006_and_a002_november(real) -> None:
    result = compute_schedule(real, sample_confirmed(), "2026-11")
    assert result.dates_of("B006") == [D(2026, 11, 1), D(2026, 11, 29)]
    assert result.dates_of("A002") == [D(2026, 11, 5)]


def test_reference_rule_same_as_closing_day(real) -> None:
    """결산 기준일 = 마감일과 같음 → '결산 기준일 D+2 영업일'은 A002와 같은 11-05."""
    from src.parsing import Job
    confirmed = sample_confirmed()
    confirmed.base_date_rules["결산 기준일"] = "마감일과 같음"
    real.jobs.append(Job("T001", "결산 테스트", "결산 기준일 D+2 영업일", "16:30", "", "x"))
    result = compute_schedule(real, confirmed, "2026-11")
    assert result.dates_of("T001") == result.dates_of("A002") == [D(2026, 11, 5)]


def test_rules_are_month_independent(real) -> None:
    """같은 확정 규칙으로 11월·12월 모두 계산된다 (달마다 다시 확정하지 않음)."""
    confirmed = sample_confirmed()
    nov = compute_schedule(real, confirmed, "2026-11")
    dec = compute_schedule(real, confirmed, "2026-12")
    assert not nov.blocked and not dec.blocked
    assert nov.unconfirmed == dec.unconfirmed == {}


# ------------------------------------------------------------ 겹침 제외 (2025-10 추석 연휴 fixture)

@pytest.fixture
def oct2025(real):
    real.calendar = fixture_calendar("calendar-2025.md")
    return real


def test_overlap_exclusion_2025_10(oct2025) -> None:
    bdates = base_dates(oct2025.calendar)
    five = bdates.occurrence("자동이체 5일자 응당일", "2025-10")
    ten = bdates.occurrence("자동이체 10일자 응당일", "2025-10")
    assert five.date == ten.date == D(2025, 10, 10)
    assert not five.excluded and ten.excluded


def test_overlap_exclusion_jobs_2025_10(oct2025) -> None:
    result = compute_schedule(oct2025, sample_confirmed(), "2025-10")
    assert not result.blocked, result.findings
    assert result.dates_of("B001") == [D(2025, 10, 9)]          # 5일자 청구 10-10 00:00 → 10-09
    assert result.dates_of("B002") == []                        # 10일자 청구 없음
    assert result.dates_of("B101") == [D(2025, 10, 12)]         # 5일자 반송 10-13 → 10-12
    assert result.dates_of("B102") == []                        # 10일자 반송 없음
    b202 = sorted({e.run_date for e in result.entries if e.job_id == "B202"})
    assert D(2025, 10, 11) not in b202                          # 10일자분(10-10+1일) 없음
    assert D(2025, 10, 21) in b202                              # 20일자분은 있음
    f001 = sorted({e.run_date for e in result.entries if e.job_id == "F001"})
    assert D(2025, 10, 2) in f001                               # 5일자분(10-10의 D-1 영업일)만 수행
    excluded = [n for n in ("자동이체 5일자 응당일", "자동이체 10일자 응당일")
                if base_dates(oct2025.calendar).occurrence(n, "2025-10").excluded]
    assert excluded == ["자동이체 10일자 응당일"]                # F001의 10일자분은 계산 대상에서 빠짐
