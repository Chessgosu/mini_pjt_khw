"""02단계 ac_sample_202611: 2026-11 등록 일정이 샘플 정답 xlsx의 O 85개와 정확히 같다."""
from pathlib import Path

from openpyxl import load_workbook

from src.engine import compute_schedule
from src.parsing import load_inputs
from tests.helpers import sample_confirmed

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "correct" / "BATCH_SCHEDULE_202611.xlsx"


def expected_marks() -> set[tuple[str, int]]:
    ws = load_workbook(SAMPLE, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    days = rows[0][1:]
    return {(r[0], int(days[i])) for r in rows[1:] for i, v in enumerate(r[1:]) if v == "O"}


def test_matches_sample_85_marks(data_dir: Path) -> None:
    result = compute_schedule(load_inputs(data_dir), sample_confirmed(), "2026-11")
    assert not result.blocked, result.findings
    got = {(e.job_id, e.registration_date.day) for e in result.entries}
    expected = expected_marks()
    assert len(expected) == 85
    assert got - expected == set(), f"초과: {sorted(got - expected)}"
    assert expected - got == set(), f"누락: {sorted(expected - got)}"


def test_sample_spot_checks(data_dir: Path) -> None:
    result = compute_schedule(load_inputs(data_dir), sample_confirmed(), "2026-11")
    day = lambda job: [d.day for d in result.dates_of(job)]
    assert day("B006") == [1, 29]
    assert day("B104") == [16]
    assert day("D001") == list(range(1, 31))
    assert len(day("E001")) == 22
    assert day("C001") == [6] and day("C002") == [13]
    from datetime import date
    assert result.jobs_on(date(2026, 11, 25)) == ["B106", "B201", "D001", "E001"]
