"""10단계 ac_evaluation(오프라인 부분): 자체 평가의 판정 로직을 가짜 LLM 응답으로 확인한다.
실제 점수(통과율·RAGAS)는 evaluation/run_eval.py를 실제 Bedrock으로 수동 실행해 얻는다."""
from pathlib import Path

from evaluation.run_eval import (
    CaseResult, grade, judge_check, load_cases, required_tokens, run_cases, summarize, token_check, tool_check,
)
from src.chat import OUT_OF_SCOPE, PII_REFUSED
from tests.chat_helpers import make_chat

CASES = {c["id"]: c for c in load_cases()}


def ok_judgement(case: dict) -> dict:
    split = lambda s: [t.strip() for t in s.split(";") if t.strip()]
    return {"traits": [{"item": t, "met": True, "reason": "충족"} for t in split(case["expected_traits"])],
            "forbidden": [{"item": t, "violated": False, "reason": "위반 없음"} for t in split(case["forbidden"])]}


def test_cases_file() -> None:
    cats = [c["category"] for c in CASES.values()]
    assert len(CASES) == 20 and cats.count("guardrail") == 3


def test_required_tokens() -> None:
    assert required_tokens(CASES["2"]["expected_traits"]) == ["B106", "B201", "D001", "E001"]
    assert required_tokens(CASES["14"]["expected_traits"])[0] == "2026-12-06"
    assert "CALENDAR_MISSING" in required_tokens(CASES["15"]["expected_traits"])
    assert "OUT_OF_RANGE_MONTH" in required_tokens(CASES["16"]["expected_traits"])
    assert required_tokens("정중한 존댓말") == []


def test_token_check() -> None:
    c = CASES["2"]
    assert token_check(c, "B106, B201, D001, E001입니다.", [])[0]
    assert token_check(c, "B106, B201, D001입니다.", []) == (False, ["E001"])
    assert token_check(CASES["19"], PII_REFUSED, ["PII_MASKED"])[0]                # 코드 목록도 확인 대상


def test_tool_check() -> None:
    assert tool_check(CASES["2"], "답", ["find_jobs_by_date"])[0]
    assert not tool_check(CASES["2"], "답", ["get_registration_schedule"])[0]
    assert tool_check(CASES["13"], OUT_OF_SCOPE, [])[0]                          # 범위 밖 정해진 문장은 도구 불필요
    assert tool_check(CASES["10"], OUT_OF_SCOPE, [])[0]                          # 기대 도구 없음
    ok, note = tool_check(CASES["4"], "답", ["get_registration_schedule"])     # 둘 중 하나 호출이면 통과, 미호출 기록
    assert ok and "미호출: retrieve_business_docs" in note


def test_judge_check() -> None:
    assert judge_check(ok_judgement(CASES["1"]))[0]
    bad = ok_judgement(CASES["1"])
    bad["forbidden"][0]["violated"] = True
    ok, failed = judge_check(bad)
    assert not ok and failed[0].startswith("[금지]")
    assert judge_check(None) == (False, ["AI 판정 실패"])


def test_grade_requires_all() -> None:
    c = CASES["2"]
    good = grade(c, "B106, B201, D001, E001", [], ["find_jobs_by_date"], ok_judgement(c))
    assert good["passed"]
    assert not grade(c, "B106, B201, D001", [], ["find_jobs_by_date"], ok_judgement(c))["passed"]
    assert not grade(c, "B106, B201, D001, E001", [], [], ok_judgement(c))["passed"]


def test_summary_criteria() -> None:
    def res(i, cat, passed):
        r = CaseResult(str(i), cat, "", "", [], [], [])
        r.passed = passed
        return r

    # positive 15건 + guardrail 3건 = 18건 통과
    results = [res(i, "positive", i > 1) for i in range(17)] + [res(17 + i, "guardrail", True) for i in range(3)]
    assert summarize(results, {"faithfulness": 0.85})["criteria_met"]               # 18/20, 3/3, 0.85
    assert not summarize(results, {"faithfulness": 0.79})["criteria_met"]
    results[-1].passed = False
    assert not summarize(results, {"faithfulness": 0.9})["criteria_met"]            # guardrail 하나라도 실패


def test_run_cases_with_fake_llm(data_dir: Path, fake_llm) -> None:
    """가짜 LLM으로 전체 흐름(채팅 → 판정)이 돌아가는지만 확인."""
    chat = make_chat(data_dir, fake_llm)
    fake_llm.set("eval_judge", lambda p: {"traits": [{"item": t, "met": True, "reason": "-"} for t in p["expected_traits"]],
                                          "forbidden": [{"item": t, "violated": False, "reason": "-"} for t in p["forbidden"]]})
    cases = [CASES[i] for i in ("2", "16", "19")]
    results = run_cases(chat, chat.client, cases)
    assert [r.passed for r in results] == [True, True, True]
    assert "find_jobs_by_date" in results[0].tools
    pii_payload = [p for purpose, p in fake_llm.calls if purpose == "eval_judge"][-1]
    assert "110-123-456789" not in str(pii_payload)                                # 평가 판정에도 개인정보 가림


def test_judge_with_votes(fake_llm) -> None:
    from evaluation.run_eval import judge_with_votes
    from src.agent import LlmSettings, make_client
    client = make_client(LlmSettings(model_id="m", has_credentials=True, timeout_seconds=5), fake_llm)
    good = {"traits": [{"item": "a", "met": True, "reason": "-"}], "forbidden": []}
    bad = {"traits": [{"item": "a", "met": False, "reason": "-"}], "forbidden": []}
    seq = iter([bad, good, good])
    fake_llm.set("eval_judge", lambda p: next(seq))
    j, votes = judge_with_votes(client, {"x": 1})
    assert votes == [False, True, True] and judge_check(j)[0]                     # 다수결로 통과
    seq2 = iter([bad, bad, good])
    fake_llm.set("eval_judge", lambda p: next(seq2))
    j, votes = judge_with_votes(client, {"x": 1})
    assert votes == [False, False] and not judge_check(j)[0]                      # 2표 실패면 바로 실패
    fake_llm.set("eval_judge", good)
    assert judge_with_votes(client, {"x": 1})[1] == [True]                        # 첫 판정 통과면 한 번만