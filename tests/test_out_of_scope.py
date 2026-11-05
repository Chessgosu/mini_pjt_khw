"""08단계 ac_out_of_scope: 범위 밖 질문은 AI 추론 없이 정해진 문장, 작업 요청은 거절 문장."""
from pathlib import Path

import pytest

from src.chat import ACTION_REFUSED, OUT_OF_SCOPE
from tests.chat_helpers import make_chat

SEL = "2026-11"


@pytest.mark.parametrize("q", ["오늘 날씨 어때?", "B001이 어제 정상 수행됐어?"])
def test_out_of_scope_fixed_sentence(data_dir: Path, fake_llm, q: str) -> None:
    r = make_chat(data_dir, fake_llm).ask(q, SEL)
    assert r["answer"] == OUT_OF_SCOPE
    assert fake_llm.count("chat_answer") == 0                     # 분류만 하고 답변 문장은 만들지 않음
    assert r["contexts"] == []


def test_fee_not_in_documents(data_dir: Path, fake_llm) -> None:
    """업무 문서에 없는 정보(수수료): 문서에서 근거를 못 찾으면 정해진 문장."""
    fake_llm.set("chat_answer", {"answer": "", "citations": [], "answerable": False})
    r = make_chat(data_dir, fake_llm).ask("자동이체 출금 수수료는 얼마야?", SEL)
    assert r["answer"] == OUT_OF_SCOPE


def test_fee_invented_answer_rejected(data_dir: Path, fake_llm) -> None:
    """AI가 수수료를 지어내도(원문에 없는 인용) 표시되지 않는다."""
    fake_llm.set("chat_answer", {"answer": "수수료는 500원이에요.", "citations": ["수수료는 건당 500원입니다."],
                                 "answerable": True})
    r = make_chat(data_dir, fake_llm).ask("자동이체 출금 수수료는 얼마야?", SEL)
    assert "500원" not in r["answer"]


def test_no_documents_found_is_out_of_scope(data_dir: Path, fake_llm) -> None:
    fake_llm.set("chat_conditions", {"kind": "business"})
    r = make_chat(data_dir, fake_llm).ask("오늘 점심 메뉴 추천", SEL)
    assert r["answer"] == OUT_OF_SCOPE and fake_llm.count("chat_answer") == 0


@pytest.mark.parametrize("q", ["외부 스케줄링 사이트에 B001 등록해줘", "B001 배치 지금 실행해줘", "A001 재실행 부탁해"])
def test_action_requests_refused_before_ai(data_dir: Path, fake_llm, q: str) -> None:
    r = make_chat(data_dir, fake_llm).ask(q, SEL)
    assert r["answer"] == ACTION_REFUSED and fake_llm.count() == 0


def test_action_classified_by_ai(data_dir: Path, fake_llm) -> None:
    fake_llm.set("chat_conditions", {"kind": "action"})
    assert make_chat(data_dir, fake_llm).ask("B001 좀 돌려볼래?", SEL)["answer"] == ACTION_REFUSED


def test_registration_question_is_not_action(data_dir: Path, fake_llm) -> None:
    """'등록돼?'(질문)는 작업 요청이 아니다."""
    r = make_chat(data_dir, fake_llm).ask("B001은 12월에 언제 등록돼?", SEL)
    assert r["answer"] != ACTION_REFUSED and "2026-12-06" in r["answer"]
