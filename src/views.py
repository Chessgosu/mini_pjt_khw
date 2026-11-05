"""화면용 데이터 (09단계). Streamlit 없이 테스트할 수 있도록 화면 그리기 전 단계를 순수 함수로 둔다.

세 화면(등록 일정 매트릭스·검증·채팅)은 모두 load_state()의 같은 계산 결과를 쓴다 (단일 출처).
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from src.engine import Confirmed, ScheduleResult, compute_schedule, last_day, ym_shift
from src.parsing import Inputs, load_inputs
from src.store import ConfirmedStore, cleanup_temp_files

UNCONFIRMED_LABEL = "미확정, 확인 필요"
CSS = """
<style>
.bs-wrap{overflow-x:auto}
table.bs{border-collapse:collapse;font-size:12px}
table.bs th,table.bs td{border:1px solid #d0d7de;padding:2px 4px;text-align:center;min-width:20px}
table.bs th.job,table.bs td.job{text-align:left;white-space:nowrap;position:sticky;left:0;background:#fff}
table.bs td.reg{background:#dbeafe;font-weight:600}
table.bs th.we{color:#b91c1c}
table.bs tr.unconfirmed td{background:#f3f4f6;color:#6b7280}
table.bs td.verdict-ok.mark{background:#bbf7d0}
table.bs td.verdict-missing{background:#fecaca;font-weight:700}
table.bs td.verdict-excess{background:#fed7aa;font-weight:700}
</style>
"""


@dataclass
class AppState:
    data_dir: Path
    year_month: str
    inputs: Inputs
    confirmed: Confirmed
    result: ScheduleResult
    corrupt: bool
    invalidated: list


def load_state(data_dir: Path, year_month: str) -> AppState:
    """MD·확정값을 다시 읽어 그 달을 계산한다. 화면 세 곳이 모두 이 결과를 쓴다."""
    data_dir = Path(data_dir)
    inputs = load_inputs(data_dir)
    loaded = ConfirmedStore(data_dir).load(inputs)
    result = compute_schedule(inputs, loaded.confirmed, year_month)
    return AppState(data_dir, year_month, inputs, loaded.confirmed, result, loaded.corrupt, loaded.invalidated)


def startup(data_dir: Path) -> int:
    """앱 시작 시: 저장 도중 끊겨 남은 임시 파일 정리."""
    return cleanup_temp_files(Path(data_dir))


def month_options(inputs: Inputs, around: str) -> list[str]:
    """고를 수 있는 달: 달력에 있는 달과 그 앞뒤 한 달, 기준 달의 앞 1개월 ~ 뒤 11개월.
    달력이 부족한 달도 고를 수 있어야 CALENDAR_MISSING과 없는 달을 볼 수 있다."""
    months = {ym_shift(m, d) for m in inputs.calendar.months for d in (-1, 0, 1)}
    months |= {ym_shift(around, i) for i in range(-1, 12)}
    return sorted(months)


# ================================================================ 등록 일정 매트릭스


def schedule_matrix(state: AppState) -> dict:
    ym = state.year_month
    days = last_day(ym).day
    res = state.result
    rows = []
    for job in state.inputs.jobs:
        marked = {d.day for d in res.dates_of(job.job_id)}
        codes = [c.value for c in res.unconfirmed.get(job.job_id, [])]
        rows.append({"job_id": job.job_id, "name": job.name, "start_time": job.start_time,
                     "status": "unconfirmed" if codes else "ok", "codes": codes,
                     "cells": [d in marked for d in range(1, days + 1)]})
    return {"year_month": ym, "days": days, "rows": rows,
            "blocked": [{"code": f.code.value, "message": f.message, "missing_month": f.missing_month}
                        for f in res.findings],
            "missing_months": sorted({f.missing_month for f in res.findings if f.missing_month})}


def jobs_on_day(matrix: dict, day: int) -> list[str]:
    return [r["job_id"] for r in matrix["rows"] if r["status"] == "ok" and r["cells"][day - 1]]


def _weekend_days(ym: str) -> set[int]:
    from datetime import date
    y, m = map(int, ym.split("-"))
    return {d for d in range(1, last_day(ym).day + 1) if date(y, m, d).weekday() >= 5}


def _header(ym: str, days: int) -> str:
    we = _weekend_days(ym)
    ths = "".join(f'<th class="{"we" if d in we else ""}">{d}</th>' for d in range(1, days + 1))
    return f'<tr><th class="job">JOB</th>{ths}</tr>'


def matrix_html(matrix: dict) -> str:
    days = matrix["days"]
    out = [CSS, '<div class="bs-wrap"><table class="bs">', _header(matrix["year_month"], days)]
    for r in matrix["rows"]:
        label = f'{html.escape(r["job_id"])} <small>{html.escape(r["name"])}</small>'
        if r["status"] == "unconfirmed":
            out.append(f'<tr class="unconfirmed"><td class="job">{label}</td>'
                       f'<td colspan="{days}">{UNCONFIRMED_LABEL} ({", ".join(r["codes"])})</td></tr>')
            continue
        cells = "".join('<td class="reg">O</td>' if c else "<td></td>" for c in r["cells"])
        out.append(f'<tr><td class="job">{label}</td>{cells}</tr>')
    out.append("</table></div>")
    return "".join(out)


# ================================================================ 검증 판정 매트릭스


def verdict_matrix(judged, inputs: Inputs, uploaded) -> dict:
    """JOB × 날짜 판정. 셀: status(ok|missing|excess)와 O 표시 여부."""
    days = uploaded.days
    names = {j.job_id: j.name for j in inputs.jobs}
    by = {(v.job_id, v.day.day): v.status for v in judged.verdicts}
    rows = []
    for job_id in [j.job_id for j in inputs.jobs]:
        marks = uploaded.rows.get(job_id, set())
        rows.append({"job_id": job_id, "name": names[job_id],
                     "cells": [{"status": by.get((job_id, d), "ok"), "mark": d in marks} for d in range(1, days + 1)]})
    return {"year_month": judged.year_month, "days": days, "rows": rows,
            "counts": {s: len(judged.of(s)) for s in ("ok", "missing", "excess")}}


def verdict_html(vm: dict) -> str:
    days = vm["days"]
    out = [CSS, '<div class="bs-wrap"><table class="bs">', _header(vm["year_month"], days)]
    for r in vm["rows"]:
        cells = []
        for c in r["cells"]:
            cls = f'verdict-{c["status"]}' + (" mark" if c["mark"] else "")
            text = "O" if c["mark"] else ("누락" if c["status"] == "missing" else "")
            cells.append(f'<td class="{cls}">{text}</td>')
        label = f'{html.escape(r["job_id"])} <small>{html.escape(r["name"])}</small>'
        out.append(f'<tr><td class="job">{label}</td>{"".join(cells)}</tr>')
    out.append("</table></div>")
    return "".join(out)
