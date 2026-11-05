"""05단계 ac_observability(기반 부분): LlmCall 트레이스, 트레이스 꺼짐, 로그·트레이스 마스킹.

채팅 그래프·get_registration_schedule 도구 호출이 같은 트레이스에 남는지는 08단계에서 이 파일에 추가한다.
"""
import logging

import langsmith
import pytest

from src.agent import MASK, LlmSettings, make_client, mask_text, tracing_enabled

FAST = LlmSettings(model_id="m", has_credentials=True, timeout_seconds=0.3, max_retries=1)
GOOD = {"in_scope": True, "job_id": "B001", "year": None, "month": 12, "day": None}
SECRET = "wJalrXUtnFEMI-K7MDENG-bPxRfiCY-EXAMPLEKEY"
ACCESS = "AKIAIOSFODNN7EXAMPLE"


class FakeTraceable:
    """langsmith.traceable 대신 들어가 트레이스 내용(가공된 입력·출력)을 모은다. 네트워크 없음."""

    def __init__(self) -> None:
        self.runs: list[dict] = []

    def __call__(self, run_type=None, name=None, process_inputs=None, process_outputs=None, **_):
        def deco(fn):
            def wrapper(*args, **kwargs):
                inputs = dict(zip(("purpose", "payload"), args), **kwargs)
                out = fn(*args, **kwargs)
                self.runs.append({"run_type": run_type, "name": name,
                                  "inputs": process_inputs(inputs), "outputs": process_outputs(out)})
                return out
            return wrapper
        return deco


@pytest.fixture
def secrets_env(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SECRET)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", ACCESS)


@pytest.fixture
def tracer(monkeypatch):
    t = FakeTraceable()
    monkeypatch.setattr(langsmith, "traceable", t)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    return t


def test_trace_records_llm_call(fake_llm, tracer) -> None:
    calls = {"n": 0}

    def flaky(p):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("일시 오류")
        return GOOD

    fake_llm.set("chat_conditions", flaky)
    result = make_client(FAST, fake_llm).call("chat_conditions", "B001은 12월에 언제 등록돼?")
    assert result.ok
    run = tracer.runs[-1]
    assert run["run_type"] == "llm" and run["name"] == "LlmCall:chat_conditions"
    assert run["outputs"]["attempts"] == 2 and run["outputs"]["outcome"] == "ok"
    assert run["inputs"]["payload"] == "B001은 12월에 언제 등록돼?"


def test_trace_records_failures_too(fake_llm, tracer) -> None:
    fake_llm.set("chat_conditions", "timeout")
    make_client(FAST, fake_llm).call("chat_conditions", "q")
    assert tracer.runs[-1]["outputs"]["outcome"] == "timeout" and tracer.runs[-1]["outputs"]["attempts"] == 2


def test_tracing_off_same_result_without_trace(fake_llm, monkeypatch) -> None:
    t = FakeTraceable()
    monkeypatch.setattr(langsmith, "traceable", t)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert not tracing_enabled()
    fake_llm.set("chat_conditions", GOOD)
    result = make_client(FAST, fake_llm).call("chat_conditions", "B001은 12월에 언제 등록돼?")
    assert result.ok and result.value.month == 12 and t.runs == []


def test_tracing_on_and_off_give_same_value(fake_llm, tracer, monkeypatch) -> None:
    fake_llm.set("chat_conditions", GOOD)
    on = make_client(FAST, fake_llm).call("chat_conditions", "q").value
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    off = make_client(FAST, fake_llm).call("chat_conditions", "q").value
    assert on == off


# ------------------------------------------------------------ 자격 증명은 어디에도 원문으로 남지 않음

def test_secrets_never_in_logs_or_traces(fake_llm, tracer, secrets_env, caplog) -> None:
    def leaky(p):
        raise RuntimeError(f"인증 실패 key={ACCESS} secret={SECRET}")

    fake_llm.set("chat_conditions", leaky)
    caplog.set_level(logging.DEBUG)
    result = make_client(FAST, fake_llm).call("chat_conditions", {"q": f"질문 {SECRET}"})
    logging.getLogger("batch.ai").warning("실패 상세: %s", result.call.errors)
    everywhere = caplog.text + repr(result.call) + repr(tracer.runs) + repr(fake_llm.calls)
    assert SECRET not in everywhere and ACCESS not in everywhere
    assert MASK in repr(result.call.errors)


def test_log_masking_filter(caplog, secrets_env) -> None:
    caplog.set_level(logging.INFO)
    logging.getLogger("batch.ai").info("키 %s, 전화 %s", SECRET, "010-9876-5432")
    assert SECRET not in caplog.text and "010-9876-5432" not in caplog.text


# ------------------------------------------------------------ 개인정보 마스킹 규칙

@pytest.mark.parametrize("text", ["900101-1234567", "9001011234567", "1234-5678-9012-3456", "1234567890123456",
                                  "010-1234-5678", "01012345678", "110-123-456789", "123-456789-01-234"])
def test_pii_masked(text: str) -> None:
    masked, found = mask_text(f"값 {text} 끝")
    assert text not in masked and found


@pytest.mark.parametrize("text", ["2026-11-05", "B001은 12월에 언제 등록돼?", "16:30", "D+2 영업일",
                                  "2026-12-06에 등록", "BATCH_SCHEDULE_202611.xlsx"])
def test_normal_text_untouched(text: str) -> None:
    assert mask_text(text) == (text, False)


def test_secret_masked_but_not_counted_as_pii(secrets_env) -> None:
    masked, found = mask_text(f"key {ACCESS}")
    assert ACCESS not in masked and not found
