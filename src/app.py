"""배치 작업 등록 일정 확인·검증 (Streamlit). 실행: .venv\\Scripts\\streamlit run src/app.py

화면 4개: 등록 일정 / 기준일·규칙 확인 / 검증 / 채팅. 세 화면(일정·검증·채팅)은 같은 계산 결과를 쓴다.
환경 변수: APP_DATA_DIR(기본 data/), APP_DISABLE_AI=1이면 AI를 쓰지 않는다(테스트용).
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from src.agent import CHAT_DISABLED_MESSAGE, LlmSettings, load_settings, make_client  # noqa: E402
from src.views import (  # noqa: E402
    UNCONFIRMED_LABEL, load_state, matrix_html, month_options, schedule_matrix, startup, verdict_html, verdict_matrix,
)

DATA_DIR = Path(os.environ.get("APP_DATA_DIR") or ROOT / "data")
AI_OFF = os.environ.get("APP_DISABLE_AI") == "1"

st.set_page_config(page_title="배치 등록 일정", layout="wide")


# ================================================================ 공용 자원


@st.cache_resource
def _startup(data_dir: str) -> int:
    return startup(Path(data_dir))


def _settings() -> LlmSettings:
    return LlmSettings() if AI_OFF else load_settings()


@st.cache_resource
def _client():
    return make_client(_settings())


@st.cache_resource
def _embeddings():
    if AI_OFF:
        return None
    from src.retriever import bedrock_embeddings
    try:
        return bedrock_embeddings(_settings())
    except Exception:  # noqa: BLE001 — 임베딩이 안 되면 BM25로
        return None


@st.cache_resource
def _chat_service(data_dir: str):
    from src.chat import ChatService
    return ChatService(Path(data_dir), _client(), embeddings=_embeddings())


def _default_month() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


_startup(str(DATA_DIR))

# 검증 파일의 연월로 화면을 바꿀 때 (위젯이 만들어지기 전에 값 변경)
if "_switch_ym" in st.session_state:
    st.session_state["ym"] = st.session_state.pop("_switch_ym")

# ================================================================ 사이드바: 연월 선택

first = load_state(DATA_DIR, st.session_state.get("ym", _default_month()))
options = month_options(first.inputs, _default_month())
if st.session_state.get("ym") not in options:
    st.session_state["ym"] = _default_month() if _default_month() in options else options[0]

with st.sidebar:
    st.header("배치 등록 일정")
    ym = st.selectbox("대상 연월", options, key="ym")
    st.caption(f"데이터 폴더: `{DATA_DIR}`")
    st.caption("AI: " + ("사용" if _client().available else "미설정 (직접 입력으로 확정, 채팅 사용 불가)"))

state = load_state(DATA_DIR, ym)
if state.corrupt:
    st.warning("confirmed.json을 읽을 수 없어 모든 확정값을 미확정으로 처리했어요.")

tab_schedule, tab_confirm, tab_verify, tab_chat = st.tabs(["등록 일정", "기준일·규칙 확인", "검증", "채팅"])


def show_blocked(findings: list[dict]) -> None:
    for f in findings:
        extra = f" (없는 달: {f['missing_month']})" if f.get("missing_month") else ""
        st.error(f"`{f['code']}` {f['message']}{extra}")


# ================================================================ 등록 일정


with tab_schedule:
    matrix = schedule_matrix(state)
    st.subheader(f"{ym} 작업 등록일")
    if matrix["blocked"]:
        if matrix["missing_months"]:
            st.error("CALENDAR_MISSING: 없는 달 " + ", ".join(matrix["missing_months"]))
        show_blocked(matrix["blocked"])
    else:
        unconfirmed = [r for r in matrix["rows"] if r["status"] == "unconfirmed"]
        total = sum(sum(r["cells"]) for r in matrix["rows"])
        st.caption(f"JOB {len(matrix['rows'])}개 · 등록 {total}건"
                   + (f" · {UNCONFIRMED_LABEL} {len(unconfirmed)}개 (기준일·규칙 확인 화면에서 확정)" if unconfirmed else ""))
        st.html(matrix_html(matrix))


# ================================================================ 기준일·규칙 확인


def _value_input(prop: dict, key: str):
    """수정·직접 입력 위젯. JOB 규칙은 줄마다 규칙 하나."""
    kind = prop["item"]["kind"]
    current = prop.get("value")
    if kind == "run_rule":
        text = st.text_area("규칙 (한 줄에 하나)", value="\n".join(current or []), key=key)
        return [line.strip() for line in text.splitlines() if line.strip()]
    if kind == "business_match":
        titles = state.inputs.explanation.titles if state.inputs.explanation else []
        idx = titles.index(current) if current in titles else 0
        return st.selectbox("업무 설명 절", titles, index=idx, key=key) if titles else None
    return st.text_input("기준일 규칙", value=current or "", key=key)


KIND_LABEL = {"run_rule": "JOB 규칙", "business_match": "업무 매칭", "base_date_rule": "기준일 규칙"}

with tab_confirm:
    from src.confirm import ConfirmSession, Context, plan_items

    ctx_key = f"confirm::{ym}"
    pending_items = plan_items(Context(DATA_DIR, ym, _client(), embeddings=None))
    if not pending_items:
        st.success("확정이 필요한 항목이 없어요. 한 번 확정한 규칙은 다른 달에도 그대로 쓰여요.")
        st.session_state.pop(ctx_key, None)
    else:
        st.info(f"확정이 필요한 항목 {len(pending_items)}개: "
                + ", ".join(f"{KIND_LABEL[i.kind]} {i.key}" for i in pending_items[:8])
                + (" …" if len(pending_items) > 8 else ""))
        if ctx_key not in st.session_state:
            if st.button("확인 시작", type="primary"):
                session = ConfirmSession(Context(DATA_DIR, ym, _client(), embeddings=_embeddings()),
                                         thread_id=f"ui-{ym}")
                st.session_state[ctx_key] = {"session": session, "payload": session.start()}
                st.rerun()
        else:
            box = st.session_state[ctx_key]
            payload = box["payload"]
            if payload is None:
                st.session_state.pop(ctx_key)
                st.rerun()
            prop = payload["proposal"]
            item = prop["item"]
            st.markdown(f"#### {KIND_LABEL[item['kind']]}: `{item['key']}`  ·  미확정 코드 `{item['code']}`")
            if prop["origin"] == "none":
                st.warning("AI 제안이 없어요 (" + (prop["outcome"] or "AI 미설정") + "). 아래에 직접 입력해 주세요.")
            else:
                st.write("**AI 제안**" + (" (대기 중이던 제안)" if prop["origin"] == "pending" else ""), prop["value"])
                if prop.get("evidence"):
                    (st.success if prop["evidence_ok"] else st.error)(
                        ("근거 원문: " if prop["evidence_ok"] else "근거 불일치: ") + prop["evidence"])
            if prop.get("error"):
                st.error(prop["error"])
            if payload.get("error"):
                st.error(payload["error"])
            if prop.get("preview"):
                st.caption("미리보기 (확정 전 계산, 저장하지 않음)")
                st.dataframe(prop["preview"], hide_index=True)
            value = _value_input(prop, key=f"edit::{item['id']}")
            c1, c2, c3 = st.columns(3)
            decision = None
            if c1.button("확인", disabled=not prop["confirmable"], key=f"ok::{item['id']}"):
                decision = {"action": "confirm"}
            if c2.button("수정·직접 입력으로 확정", key=f"edit-btn::{item['id']}"):
                decision = {"action": "edit", "value": value}
            if c3.button("거부", key=f"reject::{item['id']}"):
                decision = {"action": "reject"}
            if decision:
                box["payload"] = box["session"].resume(decision)
                last = (box["session"].results or [{}])[-1]
                if last.get("status") == "save_failed":
                    st.session_state["_flash"] = f"CONFIRM_SAVE_FAILED: {last.get('message')}"
                st.rerun()
    if msg := st.session_state.pop("_flash", None):
        st.error(msg)


# ================================================================ 검증


with tab_verify:
    from src.verify import verify_upload

    up = st.file_uploader("등록 내역 파일 (BATCH_SCHEDULE_YYYYMM.xlsx)", type=["xlsx"])
    if up is not None:
        outcome = verify_upload(DATA_DIR, up.getvalue(), up.name, state.confirmed)
        if outcome.message:
            st.error(outcome.message)
            for f in outcome.validation.findings:
                st.write(f"- `{f.code.value}` {f.message}")
        else:
            file_ym = outcome.validation.uploaded.year_month
            if file_ym != ym:
                st.session_state["_switch_ym"] = file_ym
                st.rerun()
            judged = outcome.judged
            if judged.blocked:
                st.error("검증할 수 없어요. 먼저 아래 문제를 해결해 주세요.")
                for f in judged.blocked_by:
                    st.write(f"- `{f.code.value}` {f.message}")
            else:
                vm = verdict_matrix(judged, state.inputs, outcome.validation.uploaded)
                c = vm["counts"]
                st.subheader(f"{file_ym} 검증 결과")
                st.caption(f"누락 {c['missing']}건 · 초과 {c['excess']}건  (초록 정상 · 빨강 누락 · 주황 초과)")
                for w in judged.warnings:
                    st.warning(f"`{w.code.value}` {w.message}")
                st.html(verdict_html(vm))


# ================================================================ 채팅


with tab_chat:
    if not _client().available:
        st.info(CHAT_DISABLED_MESSAGE)
    history = st.session_state.setdefault("chat", [])
    for turn in history:
        with st.chat_message("user"):
            st.write(turn["q"])
        with st.chat_message("assistant"):
            st.write(turn["a"]["answer"])
            if turn["a"].get("codes"):
                st.caption("코드: " + ", ".join(turn["a"]["codes"]))
            with st.expander("근거·트레이스"):
                st.json({"contexts": turn["a"]["contexts"], "trace": turn["a"]["trace"]})
    q = st.chat_input("예: B001은 12월에 언제 등록돼?")
    if q:
        result = _chat_service(str(DATA_DIR)).ask(q, ym)
        from src.agent import mask_text
        history.append({"q": mask_text(q)[0], "a": result})
        st.rerun()
