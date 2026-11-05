"""07단계 ac_ai_confirm_gate: AI 제안은 확인 전에는 계산에 반영되지 않고, 확인한 값만 반영된다."""
import json
from datetime import date
from pathlib import Path

import pytest

from src.agent import LlmSettings, make_client
from src.confirm import ConfirmSession, Context, plan_items
from src.engine import compute_schedule
from src.models import Code, Finding
from tests.helpers import (
    FIXTURES, X001_RULES, install_sample_ai, sample_base_date_rules, sample_confirmed, sample_normalized_rules,
)

SETTINGS = LlmSettings(model_id="m", has_credentials=True, timeout_seconds=5, max_retries=1)


def make_ctx(data_dir: Path, fake_llm, ym: str = "2026-11", ai: bool = True) -> Context:
    client = make_client(SETTINGS, fake_llm) if ai else make_client(LlmSettings())
    return Context(data_dir, ym, client)


def run(session: ConfirmSession, policy=lambda p: {"action": "confirm"}, limit: int = 60) -> list[dict]:
    """모든 제안에 policy로 답한다. 보여 준 제안 목록을 돌려준다."""
    shown, payload = [], session.start()
    while payload and len(shown) < limit:
        shown.append(payload)
        payload = session.resume(policy(payload))
    assert payload is None, "확인 그래프가 끝나지 않음"
    return shown


def o_marks(ctx: Context, ym: str = "2026-11") -> set:
    ctx.refresh()
    r = compute_schedule(ctx.inputs, ctx.confirmed, ym)
    y, m = map(int, ym.split("-"))
    return {(e.job_id, e.registration_date.day) for e in r.entries
            if (e.registration_date.year, e.registration_date.month) == (y, m)}


def saved(data_dir: Path) -> dict:
    p = data_dir / "confirmed.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def add_x001(data_dir: Path) -> None:
    row = [line for line in (FIXTURES / "batch-schedule-x001.md").read_text(encoding="utf-8").splitlines()
           if line.startswith("| X001")][0]
    p = data_dir / "batch-schedule.md"
    p.write_text(p.read_text(encoding="utf-8").rstrip("\n") + "\n" + row + "\n", encoding="utf-8")


# ------------------------------------------------------------ 완료 기준: 제안 확인 → 1.3절 규칙 저장 → O 85개

def test_confirm_all_reproduces_sample(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    ctx = make_ctx(data_dir, fake_llm)
    shown = run(ConfirmSession(ctx))
    data = saved(data_dir)
    assert {k: v["value"] for k, v in data["base_date_rules"].items()} == sample_base_date_rules()
    assert {k: v["value"] for k, v in data["run_rules"].items()} == sample_normalized_rules()
    assert {k: v["value"] for k, v in data["business_matches"].items()} == {"마감일": "마감"}
    assert len(o_marks(ctx)) == 85
    assert len(shown) == 15 and all(p["proposal"]["confirmable"] for p in shown)
    assert not (data_dir / "pending.json").exists() or json.loads((data_dir / "pending.json").read_text("utf-8"))["items"] == {}


def test_proposal_shows_evidence_and_three_month_preview(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    shown = run(ConfirmSession(make_ctx(data_dir, fake_llm)))
    p = next(s["proposal"] for s in shown if s["proposal"]["item"]["key"] == "자동이체 5일자 응당일")
    assert p["evidence"] in (data_dir / "business-explanation.md").read_text(encoding="utf-8")
    assert [r["month"] for r in p["preview"]] == ["2026-10", "2026-11", "2026-12"]
    assert p["preview"][2]["original"] == "2026-12-05" and p["preview"][2]["date"] == "2026-12-07"
    closing = next(s["proposal"] for s in shown if s["proposal"]["item"]["key"] == "마감일"
                   and s["proposal"]["item"]["kind"] == "base_date_rule")
    assert closing["preview"][1]["date"] == "2026-11-03"          # 11-02가 자동이체 응당일 → 다음 영업일
    assert p["item"]["code"] == Code.BASE_DATE_UNCONFIRMED.value


def test_nothing_applied_before_confirm(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    ctx = make_ctx(data_dir, fake_llm)
    first = ConfirmSession(ctx).start()
    assert first["proposal"]["value"] is not None
    assert saved(data_dir) == {}                                    # 확정 파일 없음
    result = compute_schedule(ctx.inputs, ctx.confirmed, "2026-11")
    assert Code.JOB_RULE_UNCONFIRMED in result.unconfirmed[first["proposal"]["item"]["key"]]
    assert json.loads((data_dir / "pending.json").read_text("utf-8"))["items"]   # 대기 제안은 pending.json에


def test_unconfirmed_codes_per_kind(data_dir: Path, fake_llm) -> None:
    ctx = make_ctx(data_dir, fake_llm)
    kinds = {i.kind for i in plan_items(ctx)}
    assert kinds == {"run_rule", "base_date_rule"}                  # '마감일' 매칭은 A002 정규화가 확정된 뒤에 보임
    assert plan_items(ctx)[0].kind == "run_rule"
    result = compute_schedule(ctx.inputs, ctx.confirmed, "2026-11")
    assert result.unconfirmed["C001"] == [Code.JOB_RULE_UNCONFIRMED]
    assert result.unconfirmed["B001"] == [Code.BASE_DATE_UNCONFIRMED]
    ctx.store.confirm("run_rules", "A002", ["마감일 D+2 영업일"],
                      plan_items(ctx)[[i.key for i in plan_items(ctx)].index("A002")].source_hash)
    ctx.refresh()
    result = compute_schedule(ctx.inputs, ctx.confirmed, "2026-11")
    assert Code.BUSINESS_MATCH_UNCONFIRMED in result.unconfirmed["A002"]


# ------------------------------------------------------------ 다른 달, 재시작

def test_other_month_does_not_ask_again(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    run(ConfirmSession(make_ctx(data_dir, fake_llm)))
    calls = fake_llm.count()
    dec = make_ctx(data_dir, fake_llm, "2026-12")
    assert plan_items(dec) == [] and ConfirmSession(dec).start() is None
    assert fake_llm.count() == calls
    assert compute_schedule(dec.inputs, dec.confirmed, "2026-12").unconfirmed == {}


def test_reopen_reuses_pending_without_ai(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    first = ConfirmSession(make_ctx(data_dir, fake_llm)).start()           # 제안만 받고 앱 종료
    calls = fake_llm.count()
    again = ConfirmSession(make_ctx(data_dir, fake_llm), thread_id="new").start()   # 다시 열기
    assert fake_llm.count() == calls
    assert again["proposal"]["origin"] == "pending"
    assert again["proposal"]["value"] == first["proposal"]["value"]
    assert saved(data_dir) == {}


def test_changed_source_asks_ai_again(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    ConfirmSession(make_ctx(data_dir, fake_llm)).start()                    # A001 제안 대기
    p = data_dir / "batch-schedule.md"
    p.write_text(p.read_text(encoding="utf-8").replace("| 매월 첫 영업일 D일 | 02:00 |", "| 매월 첫 영업일 D일 | 03:00 |"),
                 encoding="utf-8")
    calls = fake_llm.count("normalize_rule")
    again = ConfirmSession(make_ctx(data_dir, fake_llm), thread_id="new").start()
    assert again["proposal"]["item"]["key"] == "A001" and again["proposal"]["origin"] == "ai"
    assert fake_llm.count("normalize_rule") == calls + 1


# ------------------------------------------------------------ X001: JOB 설명 → 규칙 목록

def test_x001_rules_from_description(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    ctx = make_ctx(data_dir, fake_llm)
    run(ConfirmSession(ctx))                                                # 샘플 먼저 확정
    add_x001(data_dir)
    ctx.refresh()
    result = compute_schedule(ctx.inputs, ctx.confirmed, "2026-11")
    assert result.unconfirmed["X001"] == [Code.JOB_RULE_UNCONFIRMED]       # 확정 전 X001 행은 미확정
    session = ConfirmSession(ctx, thread_id="x001")
    p = session.start()["proposal"]
    assert p["item"]["key"] == "X001" and p["item"]["purpose"] == "description_rules"
    assert p["value"] == X001_RULES and p["confirmable"]
    assert p["evidence"] == "매월 10일과 자동이체 20일자 응당일 D-1 영업일에 수행"
    assert p["preview"] == [{"run": "2026-11-10", "registration": "2026-11-10"},
                            {"run": "2026-11-19", "registration": "2026-11-19"}]
    assert session.resume({"action": "confirm"}) is None
    assert {("X001", 10), ("X001", 19)} <= o_marks(ctx)


# ------------------------------------------------------------ 근거 불일치, 수정, 거부, AI 없음

def test_evidence_mismatch_cannot_be_confirmed(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    fake_llm.set("normalize_rule", lambda p: {"job_id": p["job_id"], "rules": sample_normalized_rules()[p["job_id"]],
                                              "evidence": "규칙 파일에 없는 문장"})
    session = ConfirmSession(make_ctx(data_dir, fake_llm))
    p = session.start()
    assert not p["proposal"]["confirmable"] and p["proposal"]["error"] == "근거 불일치"
    assert p["proposal"]["evidence_ok"] is False                           # 화면에 '근거 불일치' 표시용
    again = session.resume({"action": "confirm"})
    assert again["proposal"]["item"]["id"] == p["proposal"]["item"]["id"] and "근거 불일치" in again["error"]
    assert saved(data_dir) == {}
    nxt = session.resume({"action": "edit", "value": ["첫 영업일"]})          # 직접 고쳐서 확정은 가능
    assert saved(data_dir)["run_rules"]["A001"]["value"] == ["첫 영업일"] and nxt["proposal"]["item"]["key"] != "A001"


def test_edit_base_rule_recalculates_all_months(data_dir: Path, fake_llm) -> None:
    """자동이체 응당일 휴일 보정을 '이전 영업일'로 고쳐 확정하면 모든 달의 등록일이 다시 계산된다."""
    install_sample_ai(fake_llm)

    def policy(p):
        it = p["proposal"]["item"]
        if it["kind"] == "base_date_rule" and it["key"].startswith("자동이체"):
            return {"action": "edit", "value": p["proposal"]["value"].replace("다음 영업일", "이전 영업일")}
        return {"action": "confirm"}

    ctx = make_ctx(data_dir, fake_llm)
    run(ConfirmSession(ctx), policy)
    ctx.refresh()
    dec = compute_schedule(ctx.inputs, ctx.confirmed, "2026-12")
    assert dec.dates_of("B001") == [date(2026, 12, 3)]                      # 12-05(토) → 12-04(금) 수행, 전날 등록
    base = compute_schedule(ctx.inputs, sample_confirmed(), "2026-12")
    assert base.dates_of("B001") == [date(2026, 12, 6)]


def test_invalid_edit_is_rejected(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    session = ConfirmSession(make_ctx(data_dir, fake_llm))
    session.start()
    again = session.resume({"action": "edit", "value": ["매월 첫째주 월요일"]})
    assert "표준 문법이 아닌 규칙" in again["error"] and saved(data_dir) == {}


def test_reject_keeps_unconfirmed(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    ctx = make_ctx(data_dir, fake_llm)
    shown = run(ConfirmSession(ctx), lambda p: {"action": "reject"} if p["proposal"]["item"]["key"] == "C001"
                else {"action": "confirm"})
    assert [s["proposal"]["item"]["key"] for s in shown].count("C001") == 1     # 이번 실행에서 다시 묻지 않음
    assert "C001" not in saved(data_dir)["run_rules"]
    ctx.refresh()
    assert compute_schedule(ctx.inputs, ctx.confirmed, "2026-11").unconfirmed == {"C001": [Code.JOB_RULE_UNCONFIRMED]}
    assert all(not k.startswith("run_rule|C001|") for k in ctx.pending.all())


def test_without_ai_direct_input_through_graph(data_dir: Path) -> None:
    ctx = make_ctx(data_dir, None, ai=False)
    answers = {**{j: r for j, r in sample_normalized_rules().items()}, **sample_base_date_rules(), "마감일:match": "마감"}

    def policy(p):
        prop = p["proposal"]
        assert prop["value"] is None and prop["origin"] == "none" and prop["outcome"] == "not_configured"
        it = prop["item"]
        key = f"{it['key']}:match" if it["kind"] == "business_match" else it["key"]
        return {"action": "edit", "value": answers[key]}

    run(ConfirmSession(ctx), policy)
    assert len(o_marks(ctx)) == 85


def test_confirm_without_value_is_invalid(data_dir: Path) -> None:
    session = ConfirmSession(make_ctx(data_dir, None, ai=False))
    session.start()
    assert "직접 입력" in session.resume({"action": "confirm"})["error"]


def test_unknown_section_proposal(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    fake_llm.set("business_match", lambda p: {"expr": p["expr"], "section": "정산", "evidence": "매월 첫 영업일에 진행하며"})
    shown = run(ConfirmSession(make_ctx(data_dir, fake_llm)),
                lambda p: {"action": "reject"} if not p["proposal"]["confirmable"] else {"action": "confirm"})
    match = next(s["proposal"] for s in shown if s["proposal"]["item"]["kind"] == "business_match")
    assert "'## 정산' 절이 없습니다" in match["error"]


def test_save_failure_only_that_item(data_dir: Path, fake_llm) -> None:
    install_sample_ai(fake_llm)
    ctx = make_ctx(data_dir, fake_llm)
    real = ctx.store.confirm

    def flaky(kind, key, value, source_hash):
        if key == "C002":
            return Finding(Code.CONFIRM_SAVE_FAILED, "파일이 잠겨 있음", job_id=key)
        return real(kind, key, value, source_hash)

    ctx.store.confirm = flaky
    session = ConfirmSession(ctx)
    run(session)
    failed = [r for r in session.results if r["status"] == "save_failed"]
    assert [(r["item"], r["code"]) for r in failed] == [("run_rule|C002", "CONFIRM_SAVE_FAILED")]
    ctx.refresh()
    assert compute_schedule(ctx.inputs, ctx.confirmed, "2026-11").unconfirmed == {"C002": [Code.JOB_RULE_UNCONFIRMED]}


def test_ai_failure_falls_back_to_direct_input(data_dir: Path, fake_llm) -> None:
    fake_llm.set("normalize_rule", "timeout")
    p = ConfirmSession(make_ctx(data_dir, fake_llm)).start()["proposal"]
    assert p["value"] is None and p["outcome"] == "timeout" and not p["confirmable"]
    assert not (data_dir / "pending.json").exists()                          # 실패한 제안은 남기지 않음


def test_base_rule_with_unknown_reference_is_rejected(data_dir: Path, fake_llm) -> None:
    """참조 묶음을 개별 이름 나열(·)로 쓰는 등 찾을 수 없는 참조가 있으면 확인할 수 없다 (영원히 미확정 방지)."""
    install_sample_ai(fake_llm, base_rules={
        "마감일": "첫 영업일, 자동이체 5일자 응당일·자동이체 10일자 응당일과 같으면 다음 영업일"})
    shown = run(ConfirmSession(make_ctx(data_dir, fake_llm)),
                lambda p: {"action": "reject"} if not p["proposal"]["confirmable"] else {"action": "confirm"})
    closing = next(s["proposal"] for s in shown if s["proposal"]["item"]["id"] == "base_date_rule|마감일")
    assert not closing["confirmable"] and "참조하는 기준일을 찾을 수 없습니다" in closing["error"]