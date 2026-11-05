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

    def get_registration_schedule(self, job_id: str, year_month: str) -> dict:
        inputs, _, result = self._state(year_month)
        job = inputs.job(job_id)
        base = {"job_id": job_id, "year_month": year_month, "entries": []}
        if job is None:
            return {**base, "status": "not_found"}
        base.update(job_name=job.name, rule=job.rule, start_time=job.start_time)
        if result.blocked:
            return {**base, **self._blocked(result)}
        if job_id in result.unconfirmed:
            return {**base, "status": "unconfirmed", "codes": [c.value for c in result.unconfirmed[job_id]]}
        entries = [{"registration_date": e.registration_date.isoformat(), "run_date": e.run_date.isoformat(),
                    "start_time": e.start_time} for e in result.entries if e.job_id == job_id]
        return {**base, "status": "ok", "entries": entries}

    def find_jobs_by_date(self, day: str) -> dict:
        d = date.fromisoformat(day)
        inputs, _, result = self._state(ym_of(d))
        base = {"date": day, "jobs": []}
        if result.blocked:
            return {**base, **self._blocked(result)}
        names = {j.job_id: j for j in inputs.jobs}
        jobs = [{"job_id": e.job_id, "job_name": names[e.job_id].name, "rule": names[e.job_id].rule,
                 "run_date": e.run_date.isoformat(), "start_time": e.start_time}
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


def make_tools(tools: ScheduleTools, retriever=None) -> list[Any]:
    """LangChain 도구 목록 (에이전트·트레이스용)."""
    from langchain_core.tools import tool

    @tool("get_registration_schedule")
    def get_registration_schedule(job_id: str, year_month: str) -> dict:
        """JOB ID와 연월(YYYY-MM)로 그 달의 계산된 등록일·수행일·시작 시각을 돌려준다."""
        return tools.get_registration_schedule(job_id, year_month)

    @tool("find_jobs_by_date")
    def find_jobs_by_date(day: str) -> dict:
        """날짜(YYYY-MM-DD)에 등록되는 JOB 목록을 돌려준다. 미확정 JOB은 목록에서 빠진다."""
        return tools.find_jobs_by_date(day)

    out = [get_registration_schedule, find_jobs_by_date]
    if retriever is not None:
        from src.retriever import make_retrieve_tool
        out.append(make_retrieve_tool(retriever))
    return out
