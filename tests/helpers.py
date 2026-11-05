"""테스트 공용 헬퍼: 사용자가 확정했다고 가정한 값, fixture 읽기."""
from __future__ import annotations

from pathlib import Path

from src.engine import Confirmed
from src.parsing import Calendar, Inputs, parse_calendar, parse_explanation, parse_schedule

FIXTURES = Path(__file__).resolve().parent / "fixtures"

AUTO_TRANSFER_KEYS = ["5일자", "10일자", "15일자", "20일자", "25일자", "말일자"]
AUTO_TRANSFER_SUFFIX = "휴일이면 다음 영업일, 앞선 자동이체 응당일과 겹치면 수행 안 함"


def sample_base_date_rules() -> dict[str, str]:
    """samples/expected-202611.md 1.3절의 기준일 규칙."""
    rules = {}
    for key in AUTO_TRANSFER_KEYS:
        start = "말일" if key == "말일자" else f"매월 {key[:-2]}일"
        rules[f"자동이체 {key} 응당일"] = f"{start}, {AUTO_TRANSFER_SUFFIX}"
    rules["마감일"] = "첫 영업일, 자동이체 응당일과 같으면 다음 영업일"
    return rules


def sample_normalized_rules() -> dict[str, list[str]]:
    """samples/expected-202611.md 1.2절의 정규화 결과 (사용자가 확정)."""
    return {
        "A001": ["첫 영업일"],
        "A002": ["마감일 D+2 영업일"],
        "A003": ["마감일 D+2일"],
        "B201": ["자동이체 5,15,25일자 응당일 D+1일"],
        "B202": ["자동이체 10,20,말일자 응당일 D+1일"],
        "C001": ["1째 주 토요일"],
        "C002": ["2째 주 토요일"],
    }


def sample_confirmed() -> Confirmed:
    return Confirmed(
        run_rules=sample_normalized_rules(),
        base_date_rules=sample_base_date_rules(),
        business_matches={"마감일": "마감"},
    )


def fixture_calendar(name: str) -> Calendar:
    cal, findings = parse_calendar((FIXTURES / name).read_text(encoding="utf-8"))
    assert not findings, findings
    return cal


def make_inputs(schedule_rows: str, explanation_text: str, calendar: Calendar) -> Inputs:
    """임의의 JOB 행으로 Inputs를 만든다 (fixture 계산용)."""
    header = "| JOB ID | JOB 이름 | 수행일 규칙 | 시작 시각 | JOB 설명 |\n|---|---|---|---|---|\n"
    jobs, f1 = parse_schedule(header + schedule_rows)
    explanation, f2 = parse_explanation(explanation_text)
    return Inputs(jobs, explanation, calendar, f1 + f2)
