"""00단계: 테스트 환경 자체를 확인한다."""
from pathlib import Path

import pytest

from tests.conftest import INPUT_FILES, REAL_DATA_DIR, FakeLLMError, FakeLLMTimeout


def test_data_dir_is_isolated_copy(data_dir: Path) -> None:
    for name in INPUT_FILES:
        assert (data_dir / name).read_text(encoding="utf-8") == (REAL_DATA_DIR / name).read_text(encoding="utf-8")
    assert data_dir.resolve() != REAL_DATA_DIR.resolve()
    assert not (data_dir / "confirmed.json").exists()
    assert not (data_dir / "pending.json").exists()


def test_fixture_files_exist(fixtures_dir: Path) -> None:
    for name in ("calendar-2025.md", "calendar-2026-09-virtual.md",
                 "business-explanation-settlement.md", "batch-schedule-x001.md"):
        assert (fixtures_dir / name).is_file(), name


def test_fake_llm_responses_and_failures(fake_llm) -> None:
    fake_llm.set("chat_query", {"job_id": "B001", "year_month": "2026-12", "day": None})
    assert fake_llm.invoke("chat_query", "B001은 12월에 언제 등록돼?")["job_id"] == "B001"
    fake_llm.set("base_date_rule", "error")
    with pytest.raises(FakeLLMError):
        fake_llm.invoke("base_date_rule")
    fake_llm.set("base_date_rule", "timeout")
    with pytest.raises(FakeLLMTimeout):
        fake_llm.invoke("base_date_rule")
    fake_llm.set("chat_answer", "schema_invalid")
    assert fake_llm.invoke("chat_answer") == {"__schema_invalid__": True}
    fake_llm.set("rule_normalize", lambda text: text.replace("첫째주", "1째 주"))
    assert fake_llm.invoke("rule_normalize", "매월 첫째주 토요일") == "매월 1째 주 토요일"
    assert fake_llm.count() == 5
    assert fake_llm.count("base_date_rule") == 2


def test_fake_clock_advances(fake_clock) -> None:
    start = fake_clock.now()
    fake_clock.advance(59.5)
    assert fake_clock.now() == start + 59.5
