"""채팅 그래프 (08단계): 문장 질문에 계산 결과만 근거로 답한다.

흐름 (LangGraph): guard → parse → schedule | business | (고정 문장) → compose → END
1. guard (코드, AI 호출 전): 요청 횟수(60초 10회) → 길이(500자) → 개인정보 → 데이터 변경·작업 요청
2. parse (AI): 질문 → 조건 {kind, JOB, 연·월·일}. 범위 밖이면 분류만 하고 답은 만들지 않는다.
3. schedule (코드 + 도구): 조회 달 결정·범위 확인 → get_registration_schedule / find_jobs_by_date → 정답 목록
   business (코드 + 도구): retrieve_business_docs + 그 달 기준일 → 근거 목록. 검색 결과가 없으면 범위 밖.
4. compose (AI → 코드 검증): 답변 문장 + 인용 목록. 인용이 정답 목록과 정확히 같고 날짜·JOB ID가 문장에 모두
   있어야 표시한다. 어긋나면 답을 버린다.
반환 형식은 api_spec.md: {"answer", "contexts": [{"doc_id","text"}], "trace": [{"step","input","output"}]} (+ "codes").
"""
from __future__ import annotations

import json
import operator
import re
import time
from collections import deque
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Callable, Optional, TypedDict

from src.agent import CHAT_DISABLED_MESSAGE, LlmClient, make_client, mask, mask_text, trace_span
from src.engine import last_day, ym_shift
from src.models import Code
from src.parsing import load_inputs
from src.retriever import BusinessRetriever, check_evidence
from src.tools import ScheduleTools

MAX_CHARS = 500
RATE_LIMIT, RATE_WINDOW = 10, 60.0

OUT_OF_SCOPE = "배치 작업 등록 일정 범위 밖의 내용이라 알 수 없어요."
ACTION_REFUSED = "이 서비스에서는 할 수 없는 작업이에요."
DATA_CHANGE_REFUSED = "채팅으로는 데이터를 바꿀 수 없어요. 확인 화면이나 MD 파일에서 수정해 주세요."
PII_REFUSED = "개인정보는 다루지 않아요."
TOO_LONG = "질문은 500자 이내로 입력해 주세요."
RATE_LIMITED_MSG = "요청이 많아요. 잠시 후 다시 시도해 주세요."
UNANSWERABLE = "이 질문은 답할 수 없어요."
ANSWER_REJECTED = "답변을 계산 결과와 대조하지 못해 표시하지 않았어요. 다시 질문해 주세요."
AI_FAILED = "AI 응답을 받지 못했어요. 잠시 후 다시 시도해 주세요."

DATA_CHANGE_RE = re.compile(r"확정\s*(처리|해)|바꿔|바꾸어|변경해|수정해|삭제|지워|지우|고쳐|덮어|무시하고|무시해")
ACTION_RE = re.compile(r"등록해|등록 해|실행해|실행시켜|수행해\s*줘|수행시켜|돌려\s*줘|재실행|스케줄링해|예약해")
JOB_ID_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]\d{3}(?!\d)")     # 'B002는'처럼 조사가 붙어도 찾음


# ================================================================ 요청 횟수 제한·이력 요약 (미들웨어)


class RateLimiter:
    """최근 window초 동안 limit번까지 허용. 시계는 바꿔 끼울 수 있다(테스트용 가짜 시계)."""

    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW, clock: Callable[[], float] = time.time):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits: deque[float] = deque()

    def allow(self) -> bool:
        now = self.clock()
        while self.hits and now - self.hits[0] >= self.window:
            self.hits.popleft()
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(now)
        return True


def summarize_history(history: list[tuple[str, str]], keep: int = 3, max_lines: int = 8) -> str:
    """최근 keep턴은 그대로, 그 이전은 한 줄씩 짧게 줄인다. AI 호출 없이 코드로 요약 (마스킹된 문장만 들어옴)."""
    if not history:
        return ""
    old, recent = history[:-keep], history[-keep:]
    lines = [f"(이전) Q: {q[:40]} / A: {a[:40]}" for q, a in old][-max_lines:]
    lines += [f"Q: {q}\nA: {a}" for q, a in recent]
    return "\n".join(lines)


# ================================================================ 상태


class ChatState(TypedDict, total=False):
    question: str              # 마스킹한 질문
    year_month: str            # 화면에서 고른 달
    history_summary: str
    conditions: dict
    target_ym: str
    route: str
    facts: list                # 정답 목록과 근거 (schedule: citation 있음)
    expected: list             # 정답 인용 목록 (schedule)
    sections: list             # 업무 설명 근거 (business)
    base_facts: list           # 그 달 기준일 (business)
    answer: Optional[str]
    codes: Annotated[list, operator.add]
    contexts: Annotated[list, operator.add]
    trace: Annotated[list, operator.add]


def _t(step: str, inp: Any, out: Any) -> list[dict]:
    def s(x):
        return mask(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str))
    return [{"step": step, "input": s(inp), "output": s(out)}]


def _done(answer: str, step: str, inp: Any, codes: list[str] | None = None) -> dict:
    return {"answer": answer, "route": "end", "codes": codes or [], "trace": _t(step, inp, answer)}


# ================================================================ 서비스


class ChatService:
    def __init__(self, data_dir: Path, client: LlmClient, clock: Callable[[], float] = time.time,
                 embeddings: Any = None) -> None:
        self.data_dir = Path(data_dir)
        self.client = client
        self.tools = ScheduleTools(self.data_dir)
        self.limiter = RateLimiter(clock=clock)
        self.embeddings = embeddings
        self.history: list[tuple[str, str]] = []
        self._retriever: BusinessRetriever | None = None
        self._retriever_key = None
        self.graph = self._build()

    # ---------- 공개 진입점

    def ask(self, question: str, year_month: str) -> dict:
        return trace_span("chat_graph", "chain", self._ask, question, year_month)

    def _ask(self, question: str, year_month: str) -> dict:
        state = self.graph.invoke({"question": question, "year_month": year_month,
                                   "history_summary": summarize_history(self.history),
                                   "codes": [], "contexts": [], "trace": []})
        answer = state.get("answer") or UNANSWERABLE
        self.history.append((mask_text(question)[0][:200], answer[:200]))
        return {"answer": answer, "contexts": state.get("contexts", []), "trace": state.get("trace", []),
                "codes": list(dict.fromkeys(state.get("codes", [])))}

    # ---------- 도구 (트레이스 구간)

    def _tool(self, name: str, fn: Callable, *args) -> Any:
        return trace_span(name, "tool", fn, *args)

    def retriever(self) -> BusinessRetriever | None:
        ex = load_inputs(self.data_dir).explanation
        key = tuple((s.name, s.text_hash) for s in ex.sections.values()) if ex else None
        if key != self._retriever_key:
            self._retriever = BusinessRetriever(ex, self.embeddings) if ex else None
            self._retriever_key = key
        return self._retriever

    # ---------- 그래프

    def _build(self):
        from langgraph.graph import END, START, StateGraph
        g = StateGraph(ChatState)
        g.add_node("guard", self.guard)
        g.add_node("parse", self.parse)
        g.add_node("schedule", self.schedule)
        g.add_node("business", self.business)
        g.add_node("compose", self.compose)
        g.add_edge(START, "guard")
        for node in ("guard", "parse", "schedule", "business"):
            g.add_conditional_edges(node, lambda s: s.get("route", "end"),
                                    {"parse": "parse", "schedule": "schedule", "business": "business",
                                     "compose": "compose", "end": END})
        g.add_edge("compose", END)
        return g.compile()

    def guard(self, state: ChatState) -> dict:
        """AI 호출 전 코드 가드레일. 원문 질문은 여기서만 보고, 이후 단계에는 마스킹한 질문만 넘긴다."""
        raw = state["question"] or ""
        masked, has_pii = mask_text(raw)
        if not self.limiter.allow():
            return _done(RATE_LIMITED_MSG, "guard", masked[:100], [Code.RATE_LIMITED.value])
        if len(raw) > MAX_CHARS:
            return _done(TOO_LONG, "guard", f"{len(raw)}자", [Code.INPUT_TOO_LONG.value])
        if has_pii:
            return _done(PII_REFUSED, "guard", masked, [Code.PII_MASKED.value])
        if DATA_CHANGE_RE.search(raw):
            return _done(DATA_CHANGE_REFUSED, "guard", masked)
        if ACTION_RE.search(raw):
            return _done(ACTION_REFUSED, "guard", masked)
        if not self.client.available:
            return _done(CHAT_DISABLED_MESSAGE, "guard", masked)
        return {"question": masked, "route": "parse", "trace": _t("guard", masked, "통과")}

    def parse(self, state: ChatState) -> dict:
        payload = {"question": state["question"], "year_month": state["year_month"],
                   "history_summary": state.get("history_summary", "")}
        res = self.client.call("chat_conditions", payload)
        tr = _t("parse", payload, res.value.model_dump() if res.ok else res.call.outcome)
        if not res.ok:
            return {**_done(AI_FAILED, "parse", payload), "trace": tr}
        cond = res.value.model_dump()
        if cond.get("job_id"):
            cond["job_id"] = cond["job_id"].strip().upper()
        route = {"schedule": "schedule", "business": "business"}.get(cond["kind"])
        if route is None:
            msg = {"action": ACTION_REFUSED, "data_change": DATA_CHANGE_REFUSED}.get(cond["kind"], OUT_OF_SCOPE)
            return {"answer": msg, "route": "end", "conditions": cond, "trace": tr}
        return {"conditions": cond, "route": route, "trace": tr}

    # ---------- 조회 달

    def _target_month(self, state: ChatState) -> tuple[str | None, dict | None]:
        """(조회 달, 실패 시 종료 상태). 연도 없는 월은 고른 달 포함 12개월 안에서 찾는다."""
        sel, cond = state["year_month"], state["conditions"]
        year, month = cond.get("year"), cond.get("month")
        if month is None and year is None:
            ym = sel
        elif month is None:
            return None, _done(UNANSWERABLE, "schedule", cond)
        elif year is not None:
            ym = f"{year:04d}-{month:02d}"
        else:
            ym = next(m for m in (ym_shift(sel, i) for i in range(12)) if int(m[5:]) == month)
        lo, hi = ym_shift(sel, -1), ym_shift(sel, 11)
        if not lo <= ym <= hi:
            return None, _done(f"조회할 수 있는 범위({lo} ~ {hi})를 벗어났어요.", "range", ym,
                               [Code.OUT_OF_RANGE_MONTH.value])
        return ym, None

    def _blocked_answer(self, ym: str, res: dict, step: str) -> dict:
        missing = res.get("missing_months") or []
        extra = f" (없는 달: {', '.join(missing)})" if missing else ""
        return _done(f"{ym} 일정은 계산할 수 없어요: {', '.join(res['codes'])}{extra}", step, ym, res["codes"])

    def _find_job(self, cond: dict) -> tuple[str | None, str | None]:
        """(job_id, 못 찾았을 때 안내). job_name만 있으면 JOB 이름·설명에서 하나로 좁혀질 때만 쓴다."""
        jobs = load_inputs(self.data_dir).jobs
        if cond.get("job_id"):
            jid = cond["job_id"]
            if any(j.job_id == jid for j in jobs):
                return jid, None
            return None, f"{jid}은(는) 규칙 파일(batch-schedule.md)에 없는 JOB이에요. batch-schedule.md를 확인해 주세요."
        name = (cond.get("job_name") or "").strip()
        if not name:
            return None, None
        hits = [j for j in jobs if name in j.name or j.name in name]
        if len(hits) == 1:
            return hits[0].job_id, None
        if not hits:
            return None, f"'{name}'에 해당하는 JOB이 규칙 파일(batch-schedule.md)에 없어요. batch-schedule.md를 확인해 주세요."
        return None, f"'{name}'에 해당하는 JOB이 여러 개예요({', '.join(j.job_id for j in hits)}). JOB ID로 질문해 주세요."

    # ---------- schedule

    def schedule(self, state: ChatState) -> dict:
        cond = state["conditions"]
        ym, stop = self._target_month(state)
        if stop:
            return stop
        job_id, not_found = self._find_job(cond)
        if not_found:
            return _done(not_found, "schedule", cond)
        day = cond.get("day")
        if day is not None and day > last_day(ym).day:
            return _done(f"{ym}에는 {day}일이 없어요.", "schedule", cond)

        if job_id:
            res = self._tool("get_registration_schedule", self.tools.get_registration_schedule, job_id, ym)
            tr = _t("tool:get_registration_schedule", {"job_id": job_id, "year_month": ym}, res)
            if res["status"] == "blocked":
                return {**self._blocked_answer(ym, res, "schedule"), "trace": tr}
            if res["status"] == "unconfirmed":
                msg = f"{job_id}은(는) 아직 확정되지 않은 값이 있어 답할 수 없어요({', '.join(res['codes'])}). 확인 화면에서 확정해 주세요."
                return {**_done(msg, "schedule", job_id, res["codes"]), "trace": tr}
            entries = [e for e in res["entries"]
                       if day is None or e["registration_date"] == f"{ym}-{day:02d}"]
            facts = [{"citation": f"{e['registration_date']} {job_id}", "job_id": job_id, "job_name": res["job_name"],
                      "rule": res["rule"], **e} for e in entries]
            empty = (f"{job_id}은(는) {ym}-{day:02d}에 등록되지 않아요." if day is not None
                     else f"{job_id}은(는) {ym}에 등록일이 없어요.")
        elif day is not None:
            d = f"{ym}-{day:02d}"
            res = self._tool("find_jobs_by_date", self.tools.find_jobs_by_date, d)
            tr = _t("tool:find_jobs_by_date", {"day": d}, res)
            if res["status"] == "blocked":
                return {**self._blocked_answer(ym, res, "schedule"), "trace": tr}
            facts = [{"citation": f"{d} {j['job_id']}", "registration_date": d, **j} for j in res["jobs"]]
            empty = f"{d}에 등록되는 JOB은 없어요."
        else:
            return _done(UNANSWERABLE, "schedule", cond)

        contexts = [{"doc_id": f"schedule#{f['citation'].replace(' ', '#')}",
                     "text": json.dumps(f, ensure_ascii=False)} for f in facts]
        if not facts:
            return {**_done(empty, "schedule", cond), "trace": tr}
        return {"target_ym": ym, "facts": facts, "expected": sorted(f["citation"] for f in facts),
                "sections": [], "base_facts": [], "contexts": contexts, "route": "compose", "trace": tr}

    # ---------- business

    def business(self, state: ChatState) -> dict:
        cond = state["conditions"]
        ym, stop = self._target_month(state)
        if stop:
            return stop
        retriever = self.retriever()
        if retriever is None:
            return _done(OUT_OF_SCOPE, "business", cond)
        docs = self._tool("retrieve_business_docs", retriever.search, state["question"], 2)
        tr = _t("tool:retrieve_business_docs", state["question"], [d.doc_id for d in docs])
        if not docs:
            return {**_done(OUT_OF_SCOPE, "business", cond), "trace": tr}
        # 특정 달을 물은 경우에만 그 달 기준일을 사실로 준다 (규칙을 묻는 질문에 날짜 목록을 지어내지 않도록)
        dated = any(cond.get(k) for k in ("year", "month", "day"))
        base = (self._tool("base_dates_in_month", self.tools.base_dates_in_month, ym) if dated
                else {"base_dates": [], "note": "질문에 달이 없어 기준일 날짜를 주지 않음"})
        base_facts = [{"citation": f"{b['name']} {b['date']}", **b} for b in base.get("base_dates", [])]
        sections = [{"doc_id": d.doc_id, "text": d.text} for d in docs]
        contexts = sections + [{"doc_id": f"base_date#{b['name']}#{b['date']}", "text": json.dumps(b, ensure_ascii=False)}
                               for b in base_facts]
        return {"target_ym": ym, "facts": base_facts, "expected": [], "sections": sections, "base_facts": base_facts,
                "contexts": contexts, "route": "compose", "trace": tr + _t("tool:base_dates_in_month", ym, base)}

    # ---------- compose: AI 답변 → 코드 검증

    def compose(self, state: ChatState) -> dict:
        payload = {"question": state["question"], "year_month": state["target_ym"], "facts": state["facts"],
                   "sections": state.get("sections", [])}
        res = self.client.call("chat_answer", payload)
        if not res.ok:
            return {"answer": AI_FAILED, "trace": _t("compose", payload, res.call.outcome)}
        ans = res.value
        if state.get("expected"):
            known = {f[k] for f in state["facts"] for k in ("registration_date", "run_date") if f.get(k)}
            ok, why = check_schedule_answer(ans.answer, ans.citations, state["expected"], known)
        elif not ans.answerable:
            return {"answer": OUT_OF_SCOPE, "trace": _t("compose", payload, "answerable=false")}
        else:
            ok, why = check_business_answer(ans.answer, ans.citations, state["sections"], state["base_facts"])
        tr = _t("compose", payload, ans.model_dump()) + _t("verify", ans.citations, "일치" if ok else why)
        return {"answer": ans.answer if ok else ANSWER_REJECTED, "trace": tr}


# ================================================================ 답변 검증 (코드)


def check_schedule_answer(answer: str, citations: list[str], expected: list[str],
                          known_dates: set[str] | None = None) -> tuple[bool, str]:
    """인용 목록이 정답 목록과 정확히 같고, 인용한 날짜·JOB ID가 문장에 모두 있어야 한다.
    문장의 다른 날짜(YYYY-MM-DD)는 계산 결과의 등록일·수행일이어야 한다."""
    cited = sorted(c.strip() for c in citations)
    if cited != sorted(expected):
        return False, f"인용 목록 불일치: {cited} ≠ {sorted(expected)}"
    for c in expected:
        d, job = c.split(" ", 1)
        if d not in answer or job not in answer:
            return False, f"문장에 '{c}'의 날짜나 JOB ID가 없음"
    extra = set(JOB_ID_RE.findall(answer)) - {c.split(" ", 1)[1] for c in expected}
    if extra:
        return False, f"정답 목록에 없는 JOB ID가 문장에 있음: {sorted(extra)}"
    allowed = (known_dates or set()) | {c.split(" ", 1)[0] for c in expected}
    stray = set(re.findall(r"\d{4}-\d{2}-\d{2}", answer)) - allowed
    if stray:
        return False, f"계산 결과에 없는 날짜가 문장에 있음: {sorted(stray)}"
    return True, ""


def check_business_answer(answer: str, citations: list[str], sections: list[dict], base_facts: list[dict]
                          ) -> tuple[bool, str]:
    """인용마다 업무 설명 원문 문장이거나 계산된 기준일이어야 하고, 문장의 날짜는 인용한 기준일의 날짜여야 한다."""
    if not citations:
        return False, "인용 없음"
    facts = {b["citation"]: b for b in base_facts}
    texts = [s["text"] for s in sections]
    for c in citations:
        if c in facts:
            continue
        if check_evidence(c, texts) is not None:
            return False, f"근거 불일치: {c[:40]}"
    dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", answer))
    allowed = {facts[c]["date"] for c in citations if c in facts}          # 인용한 기준일의 날짜만
    if dates - allowed:
        return False, f"인용한 기준일에 없는 날짜가 문장에 있음: {sorted(dates - allowed)}"
    return True, ""


# ================================================================ 기본 서비스 (ask 진입점용)


@lru_cache(maxsize=1)
def default_service() -> ChatService:
    from src.retriever import bedrock_embeddings
    data_dir = Path(__file__).resolve().parent.parent / "data"
    return ChatService(data_dir, make_client(), embeddings=bedrock_embeddings())
