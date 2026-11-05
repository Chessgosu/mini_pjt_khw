"""08단계 ac_chat_grounded: 답은 계산 결과만 근거로, 인용 목록은 정답 목록과 정확히 같아야 표시된다."""
from pathlib import Path

import pytest

from src.chat import AI_FAILED, ANSWER_REJECTED, UNANSWERABLE, check_schedule_answer
from src.agent import CHAT_DISABLED_MESSAGE
from src.store import ConfirmedStore
from tests.chat_helpers import make_chat

SEL = "2026-11"


def steps(result) -> list[str]:
    return [t["step"] for t in result["trace"]]


# ------------------------------------------------------------ 정답 예시 (expected-202611.md 4절)

def test_b001_in_december(data_dir: Path, fake_llm) -> None:
    r = make_chat(data_dir, fake_llm).ask("B001은 12월에 언제 등록돼?", SEL)
    assert "2026-12-06" in r["answer"] and "B001" in r["answer"]
    assert set(r) >= {"answer", "contexts", "trace"}
    assert [c["doc_id"] for c in r["contexts"]] == ["schedule#2026-12-06#B001"]
    assert steps(r) == ["guard", "parse", "tool:get_registration_schedule", "compose", "verify"]


def test_jobs_on_25th(data_dir: Path, fake_llm) -> None:
    r = make_chat(data_dir, fake_llm).ask("25일에 등록되는 배치는 뭐야?", SEL)
    for job in ("B106", "B201", "D001", "E001"):
        assert job in r["answer"]
    assert {c["doc_id"].split("#")[2] for c in r["contexts"]} == {"B106", "B201", "D001", "E001"}
    assert "tool:find_jobs_by_date" in steps(r)


@pytest.mark.parametrize("q,dates", [
    ("B006은 11월에 언제 등록돼?", ["2026-11-01", "2026-11-29"]),
    ("B201은 11월에 몇 번 등록돼?", ["2026-11-05", "2026-11-16", "2026-11-25"]),
])
def test_more_schedule_answers(data_dir: Path, fake_llm, q: str, dates: list[str]) -> None:
    r = make_chat(data_dir, fake_llm).ask(q, SEL)
    assert all(d in r["answer"] for d in dates) and r["answer"] != ANSWER_REJECTED


def test_next_month_runs_counted_on_30th(data_dir: Path, fake_llm) -> None:
    r = make_chat(data_dir, fake_llm).ask("11월 30일에 등록되는 배치는?", SEL)
    assert {c["doc_id"].split("#")[2] for c in r["contexts"]} == {"A001", "B105", "B202", "D001", "E001"}


# ------------------------------------------------------------ 월 해석

@pytest.mark.parametrize("sel,month,expected", [("2026-11", 12, "2026-12"), ("2026-11", 11, "2026-11"),
                                                ("2026-12", 11, "2027-11"), ("2026-11", 1, "2027-01")])
def test_month_without_year_within_12_months(data_dir: Path, fake_llm, sel, month, expected) -> None:
    fake_llm.set("chat_conditions", lambda p: {"kind": "schedule", "job_id": "B001", "month": month})
    r = make_chat(data_dir, fake_llm).ask("B001 등록일", sel)
    tool = next(t for t in r["trace"] if t["step"] == "tool:get_registration_schedule")
    assert f'"year_month": "{expected}"' in tool["input"]


def test_january_needs_february_calendar(data_dir: Path, fake_llm) -> None:
    r = make_chat(data_dir, fake_llm).ask("1월 B001 등록일", SEL)
    assert "CALENDAR_MISSING" in r["codes"] and "2027-02" in r["answer"]
    assert fake_llm.count("chat_answer") == 0


# ------------------------------------------------------------ 인용 검증

@pytest.mark.parametrize("bad", [
    {"answer": "B001은 2026-12-06에 등록돼요.", "citations": [], "answerable": True},
    {"answer": "B001은 2026-12-06에 등록돼요.", "citations": ["2026-12-06 B001", "2026-12-07 B001"], "answerable": True},
    {"answer": "B001은 12월 6일에 등록돼요.", "citations": ["2026-12-06 B001"], "answerable": True},
    {"answer": "B001과 B002는 2026-12-06에 등록돼요.", "citations": ["2026-12-06 B001"], "answerable": True},
])
def test_answer_not_matching_expected_is_discarded(data_dir: Path, fake_llm, bad) -> None:
    fake_llm.set("chat_answer", bad)
    r = make_chat(data_dir, fake_llm).ask("B001은 12월에 언제 등록돼?", SEL)
    assert r["answer"] == ANSWER_REJECTED
    assert r["trace"][-1]["step"] == "verify" and r["trace"][-1]["output"] != "일치"


def test_check_schedule_answer_rules() -> None:
    exp = ["2026-11-25 B106", "2026-11-25 D001"]
    assert check_schedule_answer("2026-11-25에 B106, D001이 등록돼요.", list(reversed(exp)), exp)[0]
    assert not check_schedule_answer("2026-11-25에 B106이 등록돼요.", exp, exp)[0]
    # 수행일 설명은 계산 결과의 날짜만 허용
    one = ["2026-11-06 C001"]
    assert check_schedule_answer("C001은 수행일 2026-11-07 10:00 시작이라 전날 2026-11-06에 등록돼요.", one, one,
                                 {"2026-11-06", "2026-11-07"})[0]
    assert not check_schedule_answer("C001은 2026-11-06, 다음은 2026-12-04에 등록돼요.", one, one, {"2026-11-06"})[0]


# ------------------------------------------------------------ 미확정·없는 JOB·풀 수 없는 질문·AI 없음

def test_unconfirmed_job_excluded(data_dir: Path, fake_llm) -> None:
    chat = make_chat(data_dir, fake_llm)
    ConfirmedStore(data_dir).remove("run_rules", "B201")
    r = chat.ask("25일에 등록되는 배치는 뭐야?", SEL)
    assert {c["doc_id"].split("#")[2] for c in r["contexts"]} == {"B106", "D001", "E001"}
    assert "B201" not in r["answer"]
    r = chat.ask("B201은 11월에 몇 번 등록돼?", SEL)
    assert "확정되지 않은" in r["answer"] and "JOB_RULE_UNCONFIRMED" in r["codes"]


@pytest.mark.parametrize("q,needle", [("JOB_X999는 언제 등록돼?", "X999"), ("급여 이체 준비 배치는 언제 등록돼?", "급여 이체 준비")])
def test_unknown_job(data_dir: Path, fake_llm, q: str, needle: str) -> None:
    r = make_chat(data_dir, fake_llm).ask(q, SEL)
    assert needle in r["answer"] and "batch-schedule.md" in r["answer"]
    assert fake_llm.count("chat_answer") == 0                              # 지어낸 등록일 없음


def test_unanswerable(data_dir: Path, fake_llm) -> None:
    assert make_chat(data_dir, fake_llm).ask("배치 일정 알려줘", SEL)["answer"] == UNANSWERABLE


def test_no_ai_configured(data_dir: Path, fake_llm) -> None:
    r = make_chat(data_dir, fake_llm, ai=False).ask("B001은 12월에 언제 등록돼?", SEL)
    assert r["answer"] == CHAT_DISABLED_MESSAGE


def test_ai_failure(data_dir: Path, fake_llm) -> None:
    fake_llm.set("chat_conditions", "error")
    assert make_chat(data_dir, fake_llm).ask("B001은 12월에 언제 등록돼?", SEL)["answer"] == AI_FAILED


# ------------------------------------------------------------ 업무 질문 (RAG + 기준일)

def test_business_question_with_base_date(data_dir: Path, fake_llm) -> None:
    r = make_chat(data_dir, fake_llm).ask("11월 마감일은 언제야?", SEL)
    assert "2026-11-03" in r["answer"]
    assert "business-explanation#마감" in [c["doc_id"] for c in r["contexts"]]
    assert "tool:retrieve_business_docs" in steps(r)


def test_business_answer_with_invented_sentence_rejected(data_dir: Path, fake_llm) -> None:
    fake_llm.set("chat_answer", {"answer": "마감일은 2026-11-03이에요.", "citations": ["마감은 항상 3일입니다."],
                                 "answerable": True})
    assert make_chat(data_dir, fake_llm).ask("11월 마감일은 언제야?", SEL)["answer"] == ANSWER_REJECTED


def test_business_answer_with_uncomputed_date_rejected(data_dir: Path, fake_llm) -> None:
    fake_llm.set("chat_answer", {"answer": "마감일은 2026-11-02예요.",
                                 "citations": ["매월 첫 영업일에 진행하며"], "answerable": True})
    assert make_chat(data_dir, fake_llm).ask("11월 마감일은 언제야?", SEL)["answer"] == ANSWER_REJECTED


# ------------------------------------------------------------ ask() 진입점·이력 요약

def test_agent_ask_entry(data_dir: Path, fake_llm, monkeypatch) -> None:
    import src.chat
    from src.agent import ask
    chat = make_chat(data_dir, fake_llm)
    monkeypatch.setattr(src.chat, "default_service", lambda: chat)
    r = ask("B001은 12월에 언제 등록돼?", SEL)
    assert "2026-12-06" in r["answer"] and isinstance(r["contexts"], list) and isinstance(r["trace"], list)


def test_history_summary_passed_to_parser(data_dir: Path, fake_llm) -> None:
    chat = make_chat(data_dir, fake_llm)
    for _ in range(5):
        chat.ask("B001은 12월에 언제 등록돼?", SEL)
    last = [p for purpose, p in fake_llm.calls if purpose == "chat_conditions"][-1]
    assert last["history_summary"].count("(이전)") == 1 and last["history_summary"].count("Q: ") == 4
