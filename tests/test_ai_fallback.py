"""05단계 ac_ai_fallback: AI가 없거나 실패해도 직접 입력으로 같은 결과 (O 85개)."""
import time
from pathlib import Path

import pytest

from src.agent import (
    CHAT_DISABLED_MESSAGE, MASK, ChatConditions, LlmSettings, load_settings, make_client,
)
from src.engine import compute_schedule
from src.parsing import load_inputs
from src.store import ConfirmedStore, base_date_rule_source, business_match_source, run_rule_source
from tests.helpers import sample_confirmed

FAST = LlmSettings(model_id="m", has_credentials=True, timeout_seconds=0.3, max_retries=1)
GOOD = {"in_scope": True, "job_id": "B001", "year": None, "month": 12, "day": None}


def o_marks(result, ym: str = "2026-11") -> set[tuple[str, int]]:
    y, m = map(int, ym.split("-"))
    return {(e.job_id, e.registration_date.day) for e in result.entries
            if (e.registration_date.year, e.registration_date.month) == (y, m)}


# ------------------------------------------------------------ AI 꺼짐

def test_no_credentials_means_ai_off() -> None:
    settings = load_settings({"BEDROCK_MODEL_ID": "anthropic.claude", "AWS_REGION": "us-east-1"})
    assert not settings.configured
    client = make_client(settings)
    assert not client.available
    result = client.call("chat_conditions", "B001은 12월에 언제 등록돼?")
    assert result.value is None and result.call.outcome == "not_configured" and result.call.attempts == 0


def test_settings_from_env() -> None:
    s = load_settings({"BEDROCK_MODEL_ID": " m ", "AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "b",
                       "LLM_TIMEOUT_SECONDS": "30", "LLM_MAX_RETRIES": "1", "LANGSMITH_TRACING": "True"})
    assert s.configured and s.model_id == "m" and s.timeout_seconds == 30 and s.max_retries == 1 and s.tracing
    assert load_settings({}).timeout_seconds == 30 and load_settings({}).max_retries == 1


def test_chat_disabled_message() -> None:
    assert CHAT_DISABLED_MESSAGE == "AI 미설정으로 채팅을 사용할 수 없음"


# ------------------------------------------------------------ 실패 유형별: 재시도 1회 후 None

@pytest.mark.parametrize("mode,outcome", [("error", "error"), ("timeout", "timeout"),
                                          ("schema_invalid", "schema_invalid")])
def test_failures_retry_once_then_give_up(fake_llm, mode: str, outcome: str) -> None:
    fake_llm.set("chat_conditions", mode)
    result = make_client(FAST, fake_llm).call("chat_conditions", "질문")
    assert result.value is None and result.call.outcome == outcome
    assert result.call.attempts == 2 and fake_llm.count("chat_conditions") == 2


def test_real_timeout_per_attempt(fake_llm) -> None:
    """응답이 시도당 제한 시간을 넘으면 기다리지 않고 시간 초과로 끝낸다 (합계 ≈ 2 × 제한)."""
    fake_llm.set("chat_conditions", lambda p: (time.sleep(2), GOOD)[1])
    start = time.monotonic()
    result = make_client(FAST, fake_llm).call("chat_conditions", "질문")
    assert result.call.outcome == "timeout" and result.call.attempts == 2
    assert time.monotonic() - start < 1.5


def test_retry_then_success(fake_llm) -> None:
    calls = {"n": 0}

    def flaky(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("일시 오류")
        return GOOD

    fake_llm.set("chat_conditions", flaky)
    result = make_client(FAST, fake_llm).call("chat_conditions", "질문")
    assert result.ok and result.call.attempts == 2 and isinstance(result.value, ChatConditions)
    assert result.value.month == 12


@pytest.mark.parametrize("bad", [{"in_scope": True, "month": 13}, {"in_scope": True, "extra": 1}, "문장", None,
                                 {"job_id": "B001"}])
def test_schema_mismatch_variants(fake_llm, bad) -> None:
    fake_llm.set("chat_conditions", bad)
    assert make_client(FAST, fake_llm).call("chat_conditions", "질문").call.outcome == "schema_invalid"


def test_no_retry_setting(fake_llm) -> None:
    fake_llm.set("chat_conditions", "error")
    settings = LlmSettings(model_id="m", has_credentials=True, timeout_seconds=0.3, max_retries=0)
    assert make_client(settings, fake_llm).call("chat_conditions", "q").call.attempts == 1


def test_unknown_purpose_rejected(fake_llm) -> None:
    with pytest.raises(ValueError):
        make_client(FAST, fake_llm).call("delete_files", "q")


def test_payload_masked_before_ai(fake_llm) -> None:
    seen = []
    fake_llm.set("chat_conditions", lambda p: (seen.append(p), GOOD)[1])
    make_client(FAST, fake_llm).call("chat_conditions", {"q": "내 번호 010-1234-5678, 주민 900101-1234567"})
    assert "010-1234-5678" not in str(seen) and "900101-1234567" not in str(seen) and MASK in str(seen)


# ------------------------------------------------------------ 직접 입력 경로: 같은 결과

def test_direct_input_gives_same_schedule(data_dir: Path, fake_llm) -> None:
    """AI가 모든 제안에 실패해도, 화면에서 직접 입력·확정한 값으로 2026-11 매트릭스가 O 85개와 같다."""
    for purpose in ("business_match", "base_date_rule", "normalize_rule"):
        fake_llm.set(purpose, "error")
        assert make_client(FAST, fake_llm).call(purpose, {"x": 1}).value is None

    inputs = load_inputs(data_dir)
    sample = sample_confirmed()                     # 사용자가 직접 입력한 값
    store = ConfirmedStore(data_dir)
    items = [("business_matches", e, s, business_match_source(e, inputs)) for e, s in sample.business_matches.items()]
    items += [("base_date_rules", n, r, base_date_rule_source(n, inputs, sample.business_matches))
              for n, r in sample.base_date_rules.items()]
    items += [("run_rules", j, r, run_rule_source(inputs.job(j), inputs, sample.business_matches))
              for j, r in sample.run_rules.items()]
    assert store.confirm_many(items) == []

    result = compute_schedule(inputs, store.load(inputs).confirmed, "2026-11")
    marks = o_marks(result)
    assert len(marks) == 85 and result.unconfirmed == {}
    assert {d for j, d in marks if j == "B006"} == {1, 29}
    assert {d for j, d in marks if j == "A002"} == {5}
    assert marks == o_marks(compute_schedule(inputs, sample, "2026-11"))   # AI로 확정한 경우와 같은 결과


def test_core_modules_do_not_need_ai() -> None:
    """계산·저장·검증 모듈은 AI 모듈을 가져오지 않는다."""
    src = Path(__file__).resolve().parent.parent / "src"
    for name in ("engine.py", "parsing.py", "store.py", "verify.py", "models.py"):
        text = (src / name).read_text(encoding="utf-8")
        assert "src.agent" not in text and "langchain" not in text, name
