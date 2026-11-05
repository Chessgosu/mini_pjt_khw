"""공통 테스트 fixture.

- data_dir: 실제 data/의 MD 파일을 임시 폴더에 복사한 작업 폴더 (실제 confirmed.json·pending.json을 건드리지 않음)
- fixtures_dir: tests/fixtures/ 경로
- fake_llm: 네트워크 없이 정해진 응답을 돌려주는 가짜 LLM
- fake_clock: 요청 횟수 제한(60초 창) 테스트용 가짜 시계
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_DATA_DIR = PROJECT_ROOT / "data"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
INPUT_FILES = ("batch-schedule.md", "business-explanation.md", "business-calendar.md")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """실제 입력 MD 3개만 복사한 임시 data 폴더. confirmed.json·pending.json은 복사하지 않는다."""
    target = tmp_path / "data"
    target.mkdir()
    for name in INPUT_FILES:
        shutil.copy2(REAL_DATA_DIR / name, target / name)
    return target


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


class FakeLLMError(Exception):
    """가짜 LLM이 일부러 내는 호출 실패."""


class FakeLLMTimeout(Exception):
    """가짜 LLM이 일부러 내는 시간 초과."""


@dataclass
class FakeLLM:
    """purpose별로 정해 둔 응답을 돌려주는 가짜 LLM.

    responses[purpose] 값:
      - dict/list/str: 그대로 반환
      - callable: 입력(payload)을 받아 반환값을 만든다
      - "error" / "timeout" / "schema_invalid": 해당 실패를 흉내 낸다
    calls에는 (purpose, payload) 호출 기록이 쌓인다.
    """

    responses: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def set(self, purpose: str, response: Any) -> None:
        self.responses[purpose] = response

    def invoke(self, purpose: str, payload: Any = None) -> Any:
        self.calls.append((purpose, payload))
        if purpose not in self.responses:
            raise FakeLLMError(f"가짜 LLM에 '{purpose}' 응답이 설정되지 않음")
        response = self.responses[purpose]
        if response == "error":
            raise FakeLLMError("가짜 LLM 호출 실패")
        if response == "timeout":
            raise FakeLLMTimeout("가짜 LLM 시간 초과")
        if response == "schema_invalid":
            return {"__schema_invalid__": True}
        if callable(response):
            return response(payload)
        return response

    def count(self, purpose: str | None = None) -> int:
        return sum(1 for p, _ in self.calls if purpose is None or p == purpose)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


class FakeClock:
    """초 단위로 직접 움직이는 시계. now()는 epoch 초를 돌려준다."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()
