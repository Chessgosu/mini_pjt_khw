"""09단계 ac_matrix_display: 연월을 고르면 JOB × 날짜 매트릭스, 미확정 JOB 행은 '미확정, 확인 필요'."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.agent import CHAT_DISABLED_MESSAGE
from src.store import ConfirmedStore
from src.views import UNCONFIRMED_LABEL, jobs_on_day, load_state, matrix_html, month_options, schedule_matrix
from tests.test_invalidation import save_sample

APP = str(Path(__file__).resolve().parent.parent / "src" / "app.py")


# ------------------------------------------------------------ 매트릭스 데이터

def test_matrix_2026_11(data_dir: Path) -> None:
    save_sample(data_dir)
    m = schedule_matrix(load_state(data_dir, "2026-11"))
    assert m["days"] == 30 and len(m["rows"]) == 22 and m["blocked"] == []
    assert sum(sum(r["cells"]) for r in m["rows"]) == 85
    b006 = next(r for r in m["rows"] if r["job_id"] == "B006")
    assert [d + 1 for d, c in enumerate(b006["cells"]) if c] == [1, 29]      # 1일 = 10월 말일자 응당일 11-02 청구분
    assert "B006" in jobs_on_day(m, 1)
    assert jobs_on_day(m, 30) == ["A001", "B105", "B202", "D001", "E001"]      # 12-01 수행분이 11-30에


def test_unconfirmed_rows_only(data_dir: Path) -> None:
    save_sample(data_dir)
    ConfirmedStore(data_dir).remove("run_rules", "C001")
    m = schedule_matrix(load_state(data_dir, "2026-11"))
    c001 = next(r for r in m["rows"] if r["job_id"] == "C001")
    assert c001["status"] == "unconfirmed" and c001["codes"] == ["JOB_RULE_UNCONFIRMED"]
    assert all(r["status"] == "ok" for r in m["rows"] if r["job_id"] != "C001")
    html = matrix_html(m)
    assert html.count(UNCONFIRMED_LABEL) == 1 and html.count('class="reg"') == 85 - 1


def test_nothing_confirmed_marks_ai_dependent_rows(data_dir: Path) -> None:
    m = schedule_matrix(load_state(data_dir, "2026-11"))
    unconfirmed = {r["job_id"] for r in m["rows"] if r["status"] == "unconfirmed"}
    assert {"D001", "E001"}.isdisjoint(unconfirmed)                          # 표준 규칙 JOB은 바로 표시
    assert {"A001", "B001", "C001", "F001"} <= unconfirmed


@pytest.mark.parametrize("ym,missing", [("2026-10", ["2026-09"]), ("2027-01", ["2027-02"])])
def test_calendar_missing_months(data_dir: Path, ym: str, missing: list[str]) -> None:
    save_sample(data_dir)
    m = schedule_matrix(load_state(data_dir, ym))
    assert m["missing_months"] == missing and m["blocked"][0]["code"] == "CALENDAR_MISSING"


def test_month_options_include_short_calendar_months(data_dir: Path) -> None:
    opts = month_options(load_state(data_dir, "2026-11").inputs, "2026-11")
    assert {"2026-10", "2026-11", "2026-12", "2027-01"} <= set(opts)


# ------------------------------------------------------------ 실제 화면 (AppTest, AI 끔)

@pytest.fixture
def app(data_dir: Path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("APP_DISABLE_AI", "1")
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["ym"] = "2026-11"
    return at


def test_app_shows_schedule(app, data_dir: Path) -> None:
    save_sample(data_dir)
    app.run()
    assert not app.exception
    assert [t.label for t in app.tabs] == ["등록 일정", "기준일·규칙 확인", "검증", "채팅"]
    assert any("등록 85건" in c.value for c in app.caption)


def test_app_calendar_missing(app, data_dir: Path) -> None:
    save_sample(data_dir)
    app.run()
    app.selectbox(key="ym").set_value("2027-01").run()
    assert any("CALENDAR_MISSING" in e.value and "2027-02" in e.value for e in app.error)


def test_app_chat_without_ai(app, data_dir: Path) -> None:
    save_sample(data_dir)
    app.run()
    assert any(CHAT_DISABLED_MESSAGE in i.value for i in app.info)
    app.chat_input[0].set_value("B001은 12월에 언제 등록돼?").run()
    assert app.session_state["chat"][-1]["a"]["answer"] == CHAT_DISABLED_MESSAGE


def test_app_direct_input_confirm(app, data_dir: Path) -> None:
    """AI 없이 확인 화면에서 직접 입력해 확정하면 confirmed.json에 저장되고 다른 화면에 반영된다."""
    app.run()
    app.button[0].click().run()                                           # 확인 시작
    assert any("AI 제안이 없어요" in w.value for w in app.warning)
    assert "`A001`" in app.markdown[0].value
    app.text_area[0].set_value("첫 영업일").run()
    next(b for b in app.button if b.label == "수정·직접 입력으로 확정").click().run()
    saved = json.loads((data_dir / "confirmed.json").read_text(encoding="utf-8"))
    assert saved["run_rules"]["A001"]["value"] == ["첫 영업일"]
    m = schedule_matrix(load_state(data_dir, "2026-11"))
    assert next(r for r in m["rows"] if r["job_id"] == "A001")["status"] == "ok"
