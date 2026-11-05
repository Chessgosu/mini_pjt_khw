"""04단계 ac_verdicts: 정상·누락·초과 판정, 규칙에만/파일에만 있는 JOB, 검증 연월은 파일 이름 기준."""
import io
from pathlib import Path

from openpyxl import Workbook

from src.engine import Confirmed, compute_schedule
from src.models import Code
from src.parsing import load_inputs
from src.verify import EXCESS, MISSING, OK, judge, validate_upload, verify_upload
from tests.helpers import sample_confirmed

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
NAME = "BATCH_SCHEDULE_202611.xlsx"


def run(data_dir: Path, folder: str, confirmed: Confirmed | None = None):
    path = SAMPLES / folder / NAME
    return verify_upload(data_dir, path.read_bytes(), path.name, confirmed or sample_confirmed())


def xlsx_from_schedule(data_dir: Path, ym: str, days: int) -> bytes:
    result = compute_schedule(load_inputs(data_dir), sample_confirmed(), ym)
    wb = Workbook()
    ws = wb.active
    ws.append(["JOB ID"] + list(range(1, days + 1)))
    for job in load_inputs(data_dir).jobs:
        marked = {d.day for d in result.dates_of(job.job_id)}
        ws.append([job.job_id] + ["O" if d in marked else None for d in range(1, days + 1)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_correct_sample_all_ok(data_dir: Path) -> None:
    judged = run(data_dir, "correct").judged
    assert not judged.blocked and judged.warnings == []
    assert judged.of(MISSING) == [] and judged.of(EXCESS) == []
    assert len(judged.verdicts) == 22 * 30


def test_with_errors_sample(data_dir: Path) -> None:
    judged = run(data_dir, "with-errors").judged
    assert {(v.job_id, v.day.day) for v in judged.of(MISSING)} == {("B003", 15), ("B106", 25)}
    assert {(v.job_id, v.day.day) for v in judged.of(EXCESS)} == {("C002", 14)}
    assert {(w.code, w.job_id) for w in judged.warnings} == {
        (Code.JOB_ONLY_IN_FILE, "Z999"), (Code.JOB_ONLY_IN_RULES, "B106")}
    assert not any(v.job_id == "Z999" for v in judged.verdicts)        # 파일에만 있는 JOB은 판정 제외
    assert judged.status("B003", 15) == MISSING and judged.status("A001", 2) == OK


def test_verdict_css_classes(data_dir: Path) -> None:
    judged = run(data_dir, "with-errors").judged
    assert judged.of(MISSING)[0].css_class == "verdict-missing"
    assert judged.of(EXCESS)[0].css_class == "verdict-excess"
    assert judged.of(OK)[0].css_class == "verdict-ok"


def test_year_month_comes_from_filename(data_dir: Path) -> None:
    """12월 파일 이름이면 12월 기준으로 계산·판정한다."""
    data = xlsx_from_schedule(data_dir, "2026-12", 31)
    outcome = verify_upload(data_dir, data, "BATCH_SCHEDULE_202612.xlsx", sample_confirmed())
    assert outcome.judged.year_month == "2026-12"
    assert outcome.judged.of(MISSING) == [] and outcome.judged.of(EXCESS) == []
    # 같은 내용을 11월로 올리면 헤더(31일)가 맞지 않아 잘못된 파일
    assert verify_upload(data_dir, data, NAME, sample_confirmed()).message == "잘못된 파일입니다"


def test_unconfirmed_blocks_whole_verification(data_dir: Path) -> None:
    conf = sample_confirmed()
    del conf.run_rules["C001"]
    judged = run(data_dir, "correct", conf).judged
    assert judged.blocked and judged.verdicts == []
    assert [(f.code, f.job_id) for f in judged.blocked_by] == [(Code.JOB_RULE_UNCONFIRMED, "C001")]


def test_missing_calendar_blocks_verification(data_dir: Path) -> None:
    """2027-01 파일: 2027-02 달력이 없어 판정하지 않는다 (형식은 정상)."""
    wb = Workbook()
    wb.active.append(["JOB ID"] + list(range(1, 32)))
    wb.active.append(["A001"])
    buf = io.BytesIO()
    wb.save(buf)
    outcome = verify_upload(data_dir, buf.getvalue(), "BATCH_SCHEDULE_202701.xlsx", sample_confirmed())
    assert outcome.message is None
    assert outcome.judged.blocked and outcome.judged.verdicts == []
    assert Code.CALENDAR_MISSING in {f.code for f in outcome.judged.blocked_by}


def test_judge_directly() -> None:
    """엔진 결과와 O를 날짜별로 비교하는 규칙 자체."""
    from src.engine import ScheduleEntry, ScheduleResult
    from datetime import date
    uploaded = validate_upload(_book([["J1", "O", None, "O"], ["J9", "O"]]), NAME).uploaded
    entries = [ScheduleEntry("J1", date(2026, 11, 2), None, date(2026, 11, 1)),
               ScheduleEntry("J1", date(2026, 11, 3), None, date(2026, 11, 2)),
               ScheduleEntry("J2", date(2026, 11, 6), None, date(2026, 11, 5))]
    sched = ScheduleResult("2026-11", entries, {}, [])
    judged = judge(uploaded, sched, ["J1", "J2"])
    assert judged.status("J1", 1) == OK and judged.status("J1", 2) == MISSING and judged.status("J1", 3) == EXCESS
    assert judged.status("J2", 5) == MISSING and judged.status("J9", 1) is None


def _book(rows) -> bytes:
    wb = Workbook()
    wb.active.append(["JOB ID"] + list(range(1, 31)))
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
