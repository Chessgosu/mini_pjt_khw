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


# ------------------------------------------------------------ 가짜 LLM 응답 (07단계 확인 그래프)

EVIDENCE = {
    "자동이체": "선택한 출금일자가 주말이나 공휴일이면 다음 영업일이 응당일이 됩니다.",
    "마감일": "매월 첫 영업일에 진행하며, 첫 영업일이 응당일인 경우 다음 영업일부터 진행합니다.",
    "결산 기준일": "결산의 기준일은 마감일과 같습니다.",
}
X001_RULES = ["매월 10일", "자동이체 20일자 응당일 D-1 영업일"]


def install_sample_ai(fake_llm, base_rules: dict | None = None) -> None:
    """가짜 LLM이 expected-202611.md의 값을 근거 문장과 함께 제안하도록 설정한다."""
    rules = {**sample_base_date_rules(), "결산 기준일": "마감일과 같음", **(base_rules or {})}
    normalized = sample_normalized_rules()

    def base(p):
        name = p["name"]
        ev = EVIDENCE["자동이체"] if name.startswith("자동이체") else EVIDENCE[name]
        return {"name": name, "rule": rules[name], "evidence": ev}

    def normalize(p):
        return {"job_id": p["job_id"], "rules": normalized[p["job_id"]], "evidence": p["rule"]}

    def describe(p):
        return {"job_id": p["job_id"], "rules": X001_RULES, "evidence": p["description"]}

    fake_llm.set("base_date_rule", base)
    fake_llm.set("normalize_rule", normalize)
    fake_llm.set("description_rules", describe)
    fake_llm.set("business_match", lambda p: {"expr": p["expr"], "section": "마감",
                                              "evidence": "매월 첫 영업일에 진행하며"})