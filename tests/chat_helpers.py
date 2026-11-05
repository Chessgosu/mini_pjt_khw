"""채팅 테스트 공용: 확정값을 저장한 data 폴더 + 질문별 조건을 돌려주는 가짜 LLM."""
from __future__ import annotations

from pathlib import Path

from src.agent import LlmSettings, make_client
from src.chat import ChatService
from tests.test_invalidation import save_sample

SETTINGS = LlmSettings(model_id="m", has_credentials=True, timeout_seconds=5, max_retries=1)

# 질문 → 조건 (실제 AI가 할 일을 가짜 LLM이 대신)
CONDITIONS = {
    "B001은 12월에 언제 등록돼?": {"kind": "schedule", "job_id": "B001", "month": 12},
    "25일에 등록되는 배치는 뭐야?": {"kind": "schedule", "day": 25},
    "B006은 11월에 언제 등록돼?": {"kind": "schedule", "job_id": "B006", "month": 11},
    "B201은 11월에 몇 번 등록돼?": {"kind": "schedule", "job_id": "B201", "month": 11},
    "11월 30일에 등록되는 배치는?": {"kind": "schedule", "month": 11, "day": 30},
    "1월 B001 등록일": {"kind": "schedule", "job_id": "B001", "month": 1},
    "2028년 3월 B001 등록일 알려줘": {"kind": "schedule", "job_id": "B001", "year": 2028, "month": 3},
    "JOB_X999는 언제 등록돼?": {"kind": "schedule", "job_id": "X999"},
    "급여 이체 준비 배치는 언제 등록돼?": {"kind": "schedule", "job_name": "급여 이체 준비"},
    "11월 마감일은 언제야?": {"kind": "business", "month": 11},
    "자동이체 출금 수수료는 얼마야?": {"kind": "business"},
    "오늘 날씨 어때?": {"kind": "out_of_scope"},
    "B001이 어제 정상 수행됐어?": {"kind": "out_of_scope"},
    "배치 일정 알려줘": {"kind": "schedule"},
}


def conditions_for(payload: dict) -> dict:
    cond = CONDITIONS.get(payload["question"], {"kind": "schedule", "job_id": "B001", "month": 12})
    return {"job_id": None, "job_name": None, "year": None, "month": None, "day": None, **cond}


def faithful_answer(payload: dict) -> dict:
    """정답 목록을 그대로 인용하는 '착한' 답변."""
    facts = payload["facts"]
    if payload["sections"]:                         # 업무 질문: 기준일 인용 + 원문 문장
        cites = [f["citation"] for f in facts if f["name"] == "마감일"]
        sentence = "매월 첫 영업일에 진행하며, 첫 영업일이 응당일인 경우 다음 영업일부터 진행합니다."
        dates = ", ".join(f["date"] for f in facts if f["name"] == "마감일")
        return {"answer": f"마감일은 {dates}이에요.", "citations": cites + [sentence], "answerable": True}
    text = ", ".join(f"{f['registration_date']} {f['job_id']}" for f in facts)
    return {"answer": f"등록일은 {text}입니다.", "citations": [f["citation"] for f in facts], "answerable": True}


def make_chat(data_dir: Path, fake_llm, clock=None, confirmed: bool = True, ai: bool = True) -> ChatService:
    if confirmed:
        save_sample(data_dir)
    fake_llm.set("chat_conditions", conditions_for) if "chat_conditions" not in fake_llm.responses else None
    fake_llm.set("chat_answer", faithful_answer) if "chat_answer" not in fake_llm.responses else None
    client = make_client(SETTINGS, fake_llm) if ai else make_client(LlmSettings())
    kwargs = {"clock": clock.now} if clock else {}
    return ChatService(data_dir, client, **kwargs)
