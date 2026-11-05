"""도메인 도구 (08단계, 도구 다중 패턴): 계산 엔진·검색·검증을 에이전트가 부를 수 있는 도구로 노출한다.

- get_registration_schedule(job_id, year_month): 한 JOB의 그 달 등록일
- find_jobs_by_date(date): 그 날 등록되는 JOB 목록 (미확정 JOB은 제외하고 개수만 알려 줌)
- retrieve_business_docs(query, k): 업무 설명 절 검색 (retriever.make_retrieve_tool)
- verify_registration_file(filename, content): 등록 내역 xlsx 검증 (verify.verify_upload)

도구는 data/의 MD와 확정값을 매번 다시 읽어 계산한다. 어떤 도구도 파일을 쓰지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.engine import BaseDates, BizDays, CalendarMissing, RuleSyntaxError, compute_schedule, parse_base_date_rule, \
    required_months, ym_of
from src.parsing import load_inputs
from src.store import ConfirmedStore

BLOCKING_CODES = ("MD_FILE_MISSING", "MD_SYNTAX_ERROR", "START_TIME_INVALID", "CALENDAR_MISSING")


@dataclass
class ScheduleTools:
    data_dir: Path

    def _state(self, year_month: str):
        inputs = load_inputs(Path(self.data_dir))
        confirmed = ConfirmedStore(Path(self.data_dir)).load(inputs).confirmed
        return inputs, confirmed, compute_schedule(inputs, confirmed, year_month)

    @staticmethod
    def _blocked(result) -> dict:
        return {"status": "blocked", "codes": sorted({f.code.value for f in result.findings}),
                "missing_months": sorted({f.missing_month for f in result.findings if f.missing_month})}

    @staticmethod
    def resolve_job(inputs, job_id: str | None, job_name: str | None) -> tuple[str | None, dict]:
        """JOB ID 또는 이름(이름·일부 일치가 하나일 때만)으로 JOB을 찾는다. (job_id, 실패 시 상태)."""
        if job_id:
            jid = job_id.strip().upper()
            return (jid, {}) if inputs.job(jid) else (None, {"status": "not_found", "job_id": jid})
        name = (job_name or "").strip()
        hits = [j for j in inputs.jobs if name and (name in j.name or j.name in name)]
        if len(hits) == 1:
            return hits[0].job_id, {}
        if not hits:
            return None, {"status": "not_found", "job_name": name}
        return None, {"status": "ambiguous", "job_name": name, "candidates": [j.job_id for j in hits]}

    def get_registration_schedule(self, job_id: str | None, year_month: str, job_name: str | None = None) -> dict:
        inputs, confirmed, result = self._state(year_month)
        jid, failed = self.resolve_job(inputs, job_id, job_name)
        base = {"job_id": jid or job_id, "year_month": year_month, "entries": []}
        if jid is None:
            return {**base, **failed}
        job = inputs.job(jid)
        base.update(job_id=jid, job_name=job.name, rule=job.rule, start_time=job.start_time)
        if result.blocked:
            return {**base, **self._blocked(result)}
        if jid in result.unconfirmed:
            return {**base, "status": "unconfirmed", "codes": [c.value for c in result.unconfirmed[jid]]}
        explain = Explainer(inputs, confirmed)
        entries = [{"registration_date": e.registration_date.isoformat(), "run_date": e.run_date.isoformat(),
                    "start_time": e.start_time, "basis": explain(jid, e.run_date, year_month)}
                   for e in result.entries if e.job_id == jid]
        return {**base, "status": "ok", "entries": entries}

    def find_jobs_by_date(self, day: str) -> dict:
        d = date.fromisoformat(day)
        inputs, _, result = self._state(ym_of(d))
        base = {"date": day, "jobs": []}
        if result.blocked:
            return {**base, **self._blocked(result)}
        names = {j.job_id: j for j in inputs.jobs}
        explain = Explainer(inputs, self._state(ym_of(d))[1])
        jobs = [{"job_id": e.job_id, "job_name": names[e.job_id].name, "rule": names[e.job_id].rule,
                 "run_date": e.run_date.isoformat(), "start_time": e.start_time,
                 "basis": explain(e.job_id, e.run_date, ym_of(d))}
                for e in result.entries if e.registration_date == d]
        return {**base, "status": "ok", "jobs": jobs, "unconfirmed_jobs": sorted(result.unconfirmed)}

    def base_dates_in_month(self, year_month: str) -> dict:
        """확정된 기준일 규칙으로 계산한, 그 달에 떨어지는 기준일(D) 목록 (업무 질문 답변용 사실)."""
        inputs, confirmed, result = self._state(year_month)
        if result.blocked:
            return {"year_month": year_month, "base_dates": [], **self._blocked(result)}
        rules = {}
        for n, t in confirmed.base_date_rules.items():
            try:
                rules[n] = parse_base_date_rule(n, t)
            except RuleSyntaxError:
                continue
        base = BaseDates(rules, BizDays(inputs.calendar))
        out = []
        for name in sorted(rules):
            for origin in required_months(year_month):
                try:
                    occ = base.occurrence(name, origin)
                except (CalendarMissing, RuleSyntaxError, KeyError):
                    continue
                if occ and not occ.excluded and ym_of(occ.date) == year_month:
                    out.append({"name": name, "date": occ.date.isoformat(), "original": occ.original.isoformat(),
                                "rule": confirmed.base_date_rules[name]})
        return {"year_month": year_month, "status": "ok", "base_dates": out}

    def verify_registration_file(self, filename: str, content: bytes) -> dict:
        from src.verify import verify_upload
        inputs = load_inputs(Path(self.data_dir))
        confirmed = ConfirmedStore(Path(self.data_dir)).load(inputs).confirmed
        outcome = verify_upload(Path(self.data_dir), content, filename, confirmed)
        if outcome.message:
            return {"status": "invalid_file", "message": outcome.message,
                    "codes": [c.value for c in outcome.validation.codes]}
        j = outcome.judged
        if j.blocked:
            return {"status": "blocked", "codes": sorted({f.code.value for f in j.blocked_by})}
        return {"status": "ok", "year_month": j.year_month,
                "missing": [(v.job_id, v.day.isoformat()) for v in j.of("missing")],
                "excess": [(v.job_id, v.day.isoformat()) for v in j.of("excess")],
                "warnings": [(w.code.value, w.job_id) for w in j.warnings]}


WEEKDAYS = "월화수목금토일"


def _d(x: date) -> str:
    return f"{x.isoformat()}({WEEKDAYS[x.weekday()]})"


class Explainer:
    """수행일이 어떤 규칙·기준일에서 나왔는지 설명 (채팅 답변의 근거). 계산은 엔진과 같은 함수를 쓴다."""

    def __init__(self, inputs, confirmed) -> None:
        from src.engine import known_base_names
        self.inputs, self.confirmed = inputs, confirmed
        self.bd = BizDays(inputs.calendar)
        rules = {}
        for n, t in confirmed.base_date_rules.items():
            try:
                rules[n] = parse_base_date_rule(n, t)
            except RuleSyntaxError:
                continue
        self.base = BaseDates(rules, self.bd)
        self.known = known_base_names(inputs.jobs, confirmed)

    def __call__(self, job_id: str, run: date, year_month: str) -> list[dict]:
        from src.engine import STANDARD, base_names_of, classify_rule
        job = self.inputs.job(job_id)
        c = classify_rule(job.rule)
        texts = [job.rule] if c.status == STANDARD else self.confirmed.run_rules.get(job_id, [])
        out = []
        for t in texts:
            cc = classify_rule(t)
            if cc.status != STANDARD:
                continue
            if not cc.parts.d_op:
                out.append({"rule": t, "text": f"월 기준 규칙 「{t}」 → 수행일 {_d(run)}"})
                continue
            for name in base_names_of(cc.parts, self.known):
                for origin in required_months(year_month):
                    try:
                        occ = self.base.occurrence(name, origin)
                        if occ is None or occ.excluded or cc.op.apply(occ.date, self.bd) != run:
                            continue
                    except (CalendarMissing, RuleSyntaxError, KeyError):
                        continue
                    out.append({"rule": t, "base_date": name, "original": occ.original.isoformat(),
                                "d": occ.date.isoformat(), "base_rule": self.confirmed.base_date_rules.get(name),
                                "text": f"{self.why(name, occ)}, 수행일 규칙 「{t}」 → 수행일 {_d(run)}"})
        return out

    def why(self, name: str, occ) -> str:
        """기준일이 그 날짜가 된 이유. 출발 날짜 앞에는 기준일 이름이 아니라 출발 규칙을 쓴다
        (예: '첫 영업일 11-02(월)이 자동이체 응당일과 같아 다음 영업일로 → 마감일 11-03(화)')."""
        rule = self.base.rules.get(name)
        if rule is None:
            return f"{name} {_d(occ.date)}"
        if rule.start_is_ref:
            return f"{name}은 {rule.start}과 같음 → {name} {_d(occ.date)}"
        if occ.original == occ.date:
            return f"{rule.start} {_d(occ.original)} → {name} {_d(occ.date)}"
        reasons = []
        step = occ.original
        try:
            if rule.holiday and not self.bd.is_bd(occ.original):
                step = self.bd.next_bd(step) if rule.holiday == "next" else self.bd.prev_bd(step)
                reasons.append(f"휴일이라 {'다음' if rule.holiday == 'next' else '이전'} 영업일로")
        except CalendarMissing:
            pass
        if step != occ.date and rule.overlap_next:
            reasons.append(f"{rule.overlap_next}과 같아 다음 영업일로")
        reason = (" " + ", ".join(reasons)) if reasons else " 기준일 규칙에 따라 이동"
        return f"{rule.start} {_d(occ.original)}{'이' if reasons else ''}{reason} → {name} {_d(occ.date)}"


def make_tools(tools: ScheduleTools, retriever=None) -> list[Any]:
    """LangChain 도구 목록 (에이전트·트레이스용)."""
    from langchain_core.tools import tool

    @tool("get_registration_schedule")
    def get_registration_schedule(year_month: str, job_id: str | None = None, job_name: str | None = None) -> dict:
        """JOB ID(또는 JOB 이름)와 연월(YYYY-MM)로 그 달의 계산된 등록일·수행일·시작 시각과 근거를 돌려준다."""
        return tools.get_registration_schedule(job_id, year_month, job_name)

    @tool("find_jobs_by_date")
    def find_jobs_by_date(day: str) -> dict:
        """날짜(YYYY-MM-DD)에 등록되는 JOB 목록을 돌려준다. 미확정 JOB은 목록에서 빠진다."""
        return tools.find_jobs_by_date(day)

    out = [get_registration_schedule, find_jobs_by_date]
    if retriever is not None:
        from src.retriever import make_retrieve_tool
        out.append(make_retrieve_tool(retriever))
    return out
