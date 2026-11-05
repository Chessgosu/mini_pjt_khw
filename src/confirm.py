"""확인 그래프 (07단계, HITL): AI 제안 → 근거·미리보기 표시 → 사용자 확인·수정·거부 → 확정 저장.

- 확인할 항목(plan_items): 규칙 정규화/JOB 설명→규칙, 업무 매칭, 기준일 규칙. 확정되면 다시 계획해 다음 항목으로.
- 제안(propose): 같은 근거의 대기 제안(pending.json)이 있으면 재사용하고 AI를 다시 부르지 않는다.
  없으면 LlmClient로 제안을 받고 근거 문장을 원문과 대조한다. AI를 못 쓰면 값 없이 보여 주고 직접 입력을 받는다.
- 미리보기: 기준일 규칙은 앞 달·대상 월·다음 달의 기준일, JOB 규칙은 대상 월 수행일·등록일.
- 결정(decide): 확인(근거 일치·문법 통과한 제안만), 수정(직접 입력, 문법 검사), 거부. 확정 전에는 계산에 쓰지 않는다.
- MD 원문은 AI에 데이터 필드로만 전달한다. 원문 속 지시 문장은 따르지 않으며, 따른 제안이 나와도 사용자가 확정해야만 반영된다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Optional, TypedDict

from src.agent import LlmClient
from src.engine import (
    STANDARD, BaseDates, BizDays, CalendarMissing, Confirmed, RuleSyntaxError, base_names_of, business_word,
    classify_rule, compute_schedule, group_members, known_base_names, missing_base_names, parse_base_date_rule,
    required_months,
)
from src.models import Code
from src.parsing import Inputs, load_inputs
from src.retriever import BusinessRetriever, check_evidence
from src.store import (
    ConfirmedStore, PendingStore, base_date_rule_source, business_match_source, run_rule_source, section_for,
)

RUN_GRAMMAR = (
    "수행일 규칙 표준 문법. 월 기준: '매월 N일', '말일', '첫 영업일', '마지막 영업일', 'N째 주 X요일', '매일', '매 영업일'. "
    "기준일 기준: '<기준일> <D연산>' (예 '자동이체 5일자 응당일 D일', '마감일 D+2 영업일', '자동이체 5,15,25일자 응당일 D+1일', "
    "'매 자동이체 응당일 D-1 영업일'). D연산: 'D일', 'D±N일', 'D영업일', 'D±N 영업일', 'D일 ± N영업일', '영업일 D일 ± N일'. "
    "한 JOB이 한 달에 여러 번 수행되면 규칙을 여러 개로 나눈다."
)
BASE_GRAMMAR = (
    "기준일 규칙 문법: 쉼표로 구분. ① 출발: 월 기준 규칙('매월 N일', '말일', '첫 영업일' 등) 또는 '<다른 기준일>과 같음' "
    "② 선택: '휴일이면 다음 영업일' / '휴일이면 이전 영업일' ③ 선택: '<기준일 묶음>과 같으면 다음 영업일' "
    "④ 선택: '앞선 <기준일 묶음>과 겹치면 수행 안 함'. 예: '매월 5일, 휴일이면 다음 영업일, 앞선 자동이체 응당일과 겹치면 수행 안 함'."
)
STORE_KIND = {"run_rule": "run_rules", "business_match": "business_matches", "base_date_rule": "base_date_rules"}
CODE_OF = {"run_rule": Code.JOB_RULE_UNCONFIRMED, "business_match": Code.BUSINESS_MATCH_UNCONFIRMED,
           "base_date_rule": Code.BASE_DATE_UNCONFIRMED}


# ================================================================ 항목·제안


@dataclass(frozen=True)
class Item:
    kind: str            # run_rule | business_match | base_date_rule
    key: str             # job_id / 업무 표현 / 기준일 이름
    purpose: str         # LLM 목적: normalize_rule | description_rules | business_match | base_date_rule
    source_hash: str

    @property
    def id(self) -> str:
        return f"{self.kind}|{self.key}"

    @property
    def code(self) -> Code:
        return CODE_OF[self.kind]


@dataclass
class Proposal:
    item: Item
    value: Any = None                  # 업무 매칭: 절 이름 / 기준일: 규칙 문장 / JOB: 규칙 목록
    evidence: Optional[str] = None
    evidence_ok: bool = False
    origin: str = "none"               # ai | pending | none(AI 없음·실패 → 직접 입력)
    outcome: str = ""                  # LlmCall 결과
    error: Optional[str] = None        # 근거 불일치·문법 오류 등 (있으면 '확인' 불가)
    preview: list = field(default_factory=list)

    @property
    def confirmable(self) -> bool:
        return self.value is not None and self.evidence_ok and self.error is None


@dataclass
class Context:
    data_dir: Path
    year_month: str
    client: LlmClient
    embeddings: Any = None              # None이면 BM25·제목 일치만으로 검색
    inputs: Inputs | None = None
    confirmed: Confirmed | None = None
    retriever: BusinessRetriever | None = None

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.store = ConfirmedStore(self.data_dir)
        self.pending = PendingStore(self.data_dir)
        self._retriever_key: tuple | None = None
        self.refresh()

    def refresh(self) -> None:
        """MD와 확정값을 다시 읽는다 (확정 직후·파일 수정 후). 업무 설명이 바뀐 때만 검색 색인을 다시 만든다."""
        self.inputs = load_inputs(self.data_dir)
        self.confirmed = self.store.load(self.inputs).confirmed
        ex = self.inputs.explanation
        key = tuple((s.name, s.text_hash) for s in ex.sections.values()) if ex else None
        if key != self._retriever_key:
            self.retriever = BusinessRetriever(ex, self.embeddings) if ex else None
            self._retriever_key = key


# ================================================================ 계획


def plan_items(ctx: Context) -> list[Item]:
    """지금 확인이 필요한 항목. 순서: JOB 규칙 → 업무 매칭 → 기준일 규칙 (앞 항목이 정해져야 뒤 항목이 보임)."""
    inputs, conf = ctx.inputs, ctx.confirmed
    titles = set(inputs.explanation.titles) if inputs.explanation else set()
    matches = conf.business_matches
    runs, match_items, bases = [], {}, []

    active_parts = []
    for job in inputs.jobs:
        c = classify_rule(job.rule)
        if c.status == STANDARD:
            active_parts.append(c.parts)
        elif job.job_id in conf.run_rules:
            active_parts += [cc.parts for t in conf.run_rules[job.job_id] if (cc := classify_rule(t)).status == STANDARD]
        elif c.status != "reject":
            purpose = "normalize_rule" if job.rule.strip() else "description_rules"
            runs.append(Item("run_rule", job.job_id, purpose, run_rule_source(job, inputs, matches)))

    def need_match(word: str) -> bool:
        return word not in titles and word not in matches

    d_parts = [p for p in active_parts if p and p.d_op]
    for p in d_parts:
        word = business_word(p.business_expr)
        if need_match(word) and p.business_expr not in matches:
            match_items.setdefault(word, Item("business_match", word, "business_match",
                                              business_match_source(word, inputs)))
    known = known_base_names(inputs.jobs, conf)
    names = [n for p in d_parts for n in base_names_of(p, known)]
    for name in sorted(missing_base_names(names, conf.base_date_rules, known)):
        word = business_word(name)
        src = base_date_rule_source(name, inputs, matches)
        if src is None:
            if need_match(word):
                match_items.setdefault(word, Item("business_match", word, "business_match",
                                                  business_match_source(word, inputs)))
            continue                       # 업무 매칭이 정해진 뒤에 기준일 규칙을 묻는다
        bases.append(Item("base_date_rule", name, "base_date_rule", src))
    # 묶음으로 참조될 수 있는 기준일(이름이 '<업무> <구분>' 여러 개인 것)을 먼저 물어 미리보기가 정확하도록
    bases.sort(key=lambda it: (len(group_members(" ".join([business_word(it.key)] + it.key.split()[2:]), known)) < 2,
                               it.key))
    return runs + list(match_items.values()) + bases


# ================================================================ 제안


def _payload(ctx: Context, item: Item) -> dict:
    inputs = ctx.inputs
    titles = inputs.explanation.titles if inputs.explanation else []
    known = sorted(known_base_names(inputs.jobs, ctx.confirmed))
    if item.kind == "business_match":
        cands = ctx.retriever.search(item.key, k=3) if ctx.retriever else []
        return {"expr": item.key, "titles": titles,
                "candidates": [{"section": d.section, "text": d.text} for d in cands]}
    if item.kind == "base_date_rule":
        sec = section_for(business_word(item.key), inputs, ctx.confirmed.business_matches)
        return {"name": item.key, "section_name": sec, "section_text": inputs.explanation.sections[sec].text,
                "grammar": BASE_GRAMMAR, "known_base_names": known}
    job = inputs.job(item.key)
    ctx_docs = ctx.retriever.search(job.description or job.rule, k=2) if ctx.retriever else []
    return {"job_id": job.job_id, "job_name": job.name, "rule": job.rule, "start_time": job.start_time,
            "description": job.description, "grammar": RUN_GRAMMAR, "known_base_names": known,
            "business_context": [{"section": d.section, "text": d.text} for d in ctx_docs]}


def _evidence_sources(ctx: Context, item: Item, value: Any) -> list[str]:
    inputs = ctx.inputs
    if item.kind == "business_match":
        sec = inputs.explanation.sections.get(value) if inputs.explanation and isinstance(value, str) else None
        return [sec.text] if sec else []
    if item.kind == "base_date_rule":
        sec = section_for(business_word(item.key), inputs, ctx.confirmed.business_matches)
        return [inputs.explanation.sections[sec].text] if sec else []
    job = inputs.job(item.key)
    return [job.rule, job.description]


def validate_value(ctx: Context, item: Item, value: Any) -> str | None:
    """확정 전 값 검사. 문제가 있으면 사유 문장."""
    if item.kind == "business_match":
        titles = ctx.inputs.explanation.titles if ctx.inputs.explanation else []
        return None if value in titles else f"업무 설명에 '## {value}' 절이 없습니다."
    if item.kind == "base_date_rule":
        if not isinstance(value, str) or not value.strip():
            return "기준일 규칙이 비어 있습니다."
        try:
            rule = parse_base_date_rule(item.key, value)
        except RuleSyntaxError as exc:
            return f"기준일 규칙 문법 오류: {exc}"
        known = known_base_names(ctx.inputs.jobs, ctx.confirmed) | {item.key}
        refs = ([rule.start] if rule.start_is_ref else []) + [s for s in (rule.overlap_next, rule.exclude_group) if s]
        unknown = [r for r in refs if not group_members(r, known)]
        if unknown:
            return f"참조하는 기준일을 찾을 수 없습니다: {', '.join(unknown)} ('<업무> <구분>' 묶음 이름이나 기준일 이름을 쓰세요)"
        return None
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        return "규칙 목록이 비어 있습니다."
    bad = [v for v in value if classify_rule(v).status != STANDARD]
    return f"표준 문법이 아닌 규칙: {', '.join(bad)}" if bad else None


def _value_of(item: Item, model) -> tuple[Any, str]:
    if item.kind == "business_match":
        return model.section, model.evidence
    if item.kind == "base_date_rule":
        return model.rule, model.evidence
    return list(model.rules), model.evidence


def propose(ctx: Context, item: Item) -> Proposal:
    """대기 제안 재사용 → 없으면 AI 호출. 실패하면 값 없이(직접 입력)."""
    prop = Proposal(item)
    cached = ctx.pending.get(item.kind, item.key, item.source_hash)
    if cached is not None:
        prop.value, prop.evidence, prop.origin, prop.outcome = (cached.get("value"), cached.get("evidence"),
                                                                "pending", "reused")
    else:
        result = ctx.client.call(item.purpose, _payload(ctx, item))
        prop.outcome = result.call.outcome
        if result.ok:
            prop.value, prop.evidence = _value_of(item, result.value)
            prop.origin = "ai"
            if prop.value is not None:
                ctx.pending.put(item.kind, item.key, item.source_hash,
                                {"value": prop.value, "evidence": prop.evidence})
    if prop.value is not None:
        invalid = validate_value(ctx, item, prop.value)
        mismatch = check_evidence(prop.evidence or "", _evidence_sources(ctx, item, prop.value))
        prop.evidence_ok = mismatch is None
        prop.error = invalid or mismatch
        if invalid is None:
            prop.preview = preview(ctx, item, prop.value)
    return prop


# ================================================================ 미리보기


def preview(ctx: Context, item: Item, value: Any) -> list[dict]:
    if item.kind == "base_date_rule":
        return preview_base_dates(ctx, item.key, value)
    if item.kind == "run_rule":
        return preview_run_rules(ctx, item.key, value)
    sec = ctx.inputs.explanation.sections.get(value) if ctx.inputs.explanation else None
    return [{"section": value, "text": sec.text if sec else None}]


def preview_base_dates(ctx: Context, name: str, rule_text: str) -> list[dict]:
    """이 규칙으로 계산한 앞 달·대상 월·다음 달의 기준일. 미확정 참조는 확정된 것만으로 계산."""
    texts = {**ctx.confirmed.base_date_rules, name: rule_text}
    rules = {}
    for n, t in texts.items():
        try:
            rules[n] = parse_base_date_rule(n, t)
        except RuleSyntaxError:
            continue
    base = BaseDates(rules, BizDays(ctx.inputs.calendar))
    known = known_base_names(ctx.inputs.jobs, ctx.confirmed)
    unresolved = sorted(missing_base_names([name], texts, known))
    caveat = f"미확정 기준일({', '.join(unresolved)})은 빼고 계산한 값" if unresolved else None
    out = []
    for ym in required_months(ctx.year_month):
        row = {"month": ym, "original": None, "date": None, "excluded": False, "note": caveat}
        try:
            occ = base.occurrence(name, ym)
            if occ is None:
                row["note"] = "이 달에는 없음"
            else:
                row.update(original=occ.original.isoformat(), date=occ.date.isoformat(), excluded=occ.excluded)
        except CalendarMissing as exc:
            row["note"] = f"{exc.year_month} 달력 없음"
        except (RuleSyntaxError, KeyError) as exc:
            row["note"] = f"계산 불가: {exc}"
        out.append(row)
    return out


def preview_run_rules(ctx: Context, job_id: str, rules: list[str]) -> list[dict]:
    """이 규칙 목록으로 계산한 대상 월 수행일·등록일 (확정 전 임시 계산, 저장하지 않음)."""
    conf = replace(ctx.confirmed, run_rules={**ctx.confirmed.run_rules, job_id: list(rules)})
    result = compute_schedule(ctx.inputs, conf, ctx.year_month)
    if result.blocked:
        return [{"note": "계산 불가: " + ", ".join(sorted({f.code.value for f in result.findings}))}]
    if job_id in result.unconfirmed:
        return [{"note": "먼저 확정 필요: " + ", ".join(c.value for c in result.unconfirmed[job_id])}]
    return [{"run": e.run_date.isoformat(), "registration": e.registration_date.isoformat()}
            for e in result.entries if e.job_id == job_id]


# ================================================================ 결정


def decide(ctx: Context, prop: Proposal, decision: dict) -> dict:
    """decision: {"action": "confirm"|"edit"|"reject", "value": (edit일 때)} → 결과 dict."""
    item, action = prop.item, decision.get("action")
    base = {"item": item.id, "action": action}
    if action == "reject":
        ctx.pending.discard(item.kind, item.key)
        return {**base, "status": "rejected"}
    if action == "confirm":
        if not prop.confirmable:
            return {**base, "status": "invalid",
                    "message": prop.error or "확인할 제안이 없습니다. 직접 입력해 주세요."}
        value = prop.value
    elif action == "edit":
        value = decision.get("value")
        if (err := validate_value(ctx, item, value)) is not None:
            return {**base, "status": "invalid", "message": err}
    else:
        return {**base, "status": "invalid", "message": f"알 수 없는 선택: {action}"}
    finding = ctx.store.confirm(STORE_KIND[item.kind], item.key, value, item.source_hash)
    if finding is not None:
        return {**base, "status": "save_failed", "code": finding.code.value, "message": finding.message}
    ctx.pending.discard(item.kind, item.key)
    ctx.refresh()
    return {**base, "status": "confirmed", "value": value}


# ================================================================ LangGraph 확인 그래프


class ConfirmState(TypedDict, total=False):
    handled: list[str]          # 이번 실행에서 처리한 항목 id (같은 항목을 반복해서 묻지 않음)
    current: Optional[dict]     # 지금 보여 주는 제안 (직렬화된 Proposal)
    error: Optional[str]        # 직전 결정이 무효였던 사유
    results: list[dict]


def _to_dict(prop: Proposal) -> dict:
    d = asdict(prop)
    d["item"]["id"] = prop.item.id
    d["item"]["code"] = prop.item.code.value
    d["confirmable"] = prop.confirmable
    return d


def _from_dict(d: dict) -> Proposal:
    it = d["item"]
    item = Item(it["kind"], it["key"], it["purpose"], it["source_hash"])
    return Proposal(item, d["value"], d["evidence"], d["evidence_ok"], d["origin"], d["outcome"], d["error"],
                    d["preview"])


def build_confirm_graph(ctx: Context, checkpointer=None):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    def plan(state: ConfirmState):
        ctx.refresh()
        handled = set(state.get("handled", []))
        nxt = next((i for i in plan_items(ctx) if i.id not in handled), None)
        if nxt is None:
            return Command(goto=END, update={"current": None})
        return Command(goto="propose", update={"current": {"item": asdict(nxt)}, "error": None})

    def propose_node(state: ConfirmState):
        it = state["current"]["item"]
        prop = propose(ctx, Item(it["kind"], it["key"], it["purpose"], it["source_hash"]))
        return Command(goto="review", update={"current": _to_dict(prop)})

    def review(state: ConfirmState):
        decision = interrupt({"proposal": state["current"], "error": state.get("error"),
                              "year_month": ctx.year_month})
        prop = _from_dict(state["current"])
        result = decide(ctx, prop, decision if isinstance(decision, dict) else {})
        if result["status"] == "invalid":
            return Command(goto="review", update={"error": result["message"]})
        return Command(goto="plan", update={"handled": state.get("handled", []) + [prop.item.id],
                                            "results": state.get("results", []) + [result], "error": None})

    g = StateGraph(ConfirmState)
    g.add_node("plan", plan)
    g.add_node("propose", propose_node)
    g.add_node("review", review)
    g.add_edge(START, "plan")
    return g.compile(checkpointer=checkpointer)


class ConfirmSession:
    """화면용 래퍼: start() → 첫 제안, resume(decision) → 다음 제안. 제안이 없으면 None."""

    def __init__(self, ctx: Context, thread_id: str = "confirm") -> None:
        from langgraph.checkpoint.memory import InMemorySaver
        self.ctx = ctx
        self.graph = build_confirm_graph(ctx, InMemorySaver())
        self.config = {"configurable": {"thread_id": thread_id}}
        self.state: dict = {}

    def _next(self, out: dict) -> dict | None:
        self.state = out
        intr = out.get("__interrupt__")
        return intr[0].value if intr else None

    def start(self) -> dict | None:
        return self._next(self.graph.invoke({"handled": [], "results": []}, self.config))

    def resume(self, decision: dict) -> dict | None:
        from langgraph.types import Command
        return self._next(self.graph.invoke(Command(resume=decision), self.config))

    @property
    def results(self) -> list[dict]:
        return self.graph.get_state(self.config).values.get("results", [])
