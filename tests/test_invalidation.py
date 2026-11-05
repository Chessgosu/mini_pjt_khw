"""03단계 ac_invalidation: 확정값 저장·무효화, 대기 제안, 저장 실패, 깨진 파일."""
import json
import os
from pathlib import Path

import pytest

from src.engine import compute_schedule
from src.models import Code
from src.parsing import load_inputs
from src.store import (
    ConfirmedStore, PendingStore, base_date_rule_source, business_match_source, cleanup_temp_files,
    run_rule_source,
)
from tests.helpers import sample_confirmed

REAL_CONFIRMED = Path(__file__).resolve().parent.parent / "data" / "confirmed.json"


def save_sample(data_dir: Path) -> ConfirmedStore:
    """샘플 확정값을 현재 입력의 근거 해시와 함께 저장한다."""
    inputs = load_inputs(data_dir)
    sample = sample_confirmed()
    store = ConfirmedStore(data_dir)
    items = [("business_matches", e, s, business_match_source(e, inputs)) for e, s in sample.business_matches.items()]
    items += [("base_date_rules", n, r, base_date_rule_source(n, inputs, sample.business_matches))
              for n, r in sample.base_date_rules.items()]
    items += [("run_rules", j, r, run_rule_source(inputs.job(j), inputs, sample.business_matches))
              for j, r in sample.run_rules.items()]
    assert store.confirm_many(items) == []
    return store


def edit(data_dir: Path, name: str, old: str, new: str) -> None:
    p = data_dir / name
    text = p.read_text(encoding="utf-8")
    assert old in text, old
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# ------------------------------------------------------------ 저장·읽기

def test_roundtrip_reproduces_sample(data_dir: Path) -> None:
    store = save_sample(data_dir)
    inputs = load_inputs(data_dir)
    loaded = store.load(inputs)
    assert loaded.invalidated == [] and not loaded.corrupt
    assert loaded.confirmed.base_date_rules == sample_confirmed().base_date_rules
    result = compute_schedule(inputs, loaded.confirmed, "2026-11")
    assert len(result.entries) == len(compute_schedule(inputs, sample_confirmed(), "2026-11").entries)
    assert result.unconfirmed == {}


def test_rules_reused_for_other_months(data_dir: Path) -> None:
    store = save_sample(data_dir)
    inputs = load_inputs(data_dir)
    dec = compute_schedule(inputs, store.load(inputs).confirmed, "2026-12")
    assert not dec.blocked and dec.unconfirmed == {}


def test_real_confirmed_file_untouched(data_dir: Path) -> None:
    before = REAL_CONFIRMED.exists() and REAL_CONFIRMED.read_bytes()
    save_sample(data_dir)
    after = REAL_CONFIRMED.exists() and REAL_CONFIRMED.read_bytes()
    assert before == after


# ------------------------------------------------------------ 무효화

def invalidated_after(data_dir: Path, file: str, old: str, new: str) -> set:
    store = save_sample(data_dir)
    edit(data_dir, file, old, new)
    return set(store.load(load_inputs(data_dir)).invalidated)


def test_business_section_change_invalidates_its_rules(data_dir: Path) -> None:
    inv = invalidated_after(data_dir, "business-explanation.md", "매월 5일, 10일", "매월 5일, 11일")
    assert {k for kind, k in inv if kind == "base_date_rules"} == {
        f"자동이체 {k} 응당일" for k in ("5일자", "10일자", "15일자", "20일자", "25일자", "말일자")}
    assert ("base_date_rules", "마감일") not in inv
    assert {k for kind, k in inv if kind == "run_rules"} == {"B201", "B202"}   # 자동이체 절을 참조하는 확정 규칙
    assert ("business_matches", "마감일") not in inv


def test_unrelated_section_change_keeps_everything(data_dir: Path) -> None:
    """'## 급여' 절만 바뀌면 자동이체를 참조하는 B001 등의 확정은 유지."""
    assert invalidated_after(data_dir, "business-explanation.md", "지급일은 매월 21일", "지급일은 매월 22일") == set()


def test_whitespace_only_change_keeps_everything(data_dir: Path) -> None:
    assert invalidated_after(data_dir, "business-explanation.md", "자동이체는 이자", "자동이체는   이자") == set()


def test_calendar_change_keeps_confirmation(data_dir: Path) -> None:
    inv = invalidated_after(data_dir, "business-calendar.md", "| 2026-12-25 | 성탄절 |",
                            "| 2026-12-25 | 성탄절 |\n| 2026-12-24 | 임시 휴무 |")
    assert inv == set()


def test_job_row_change_invalidates_that_job(data_dir: Path) -> None:
    inv = invalidated_after(data_dir, "batch-schedule.md", "| 매월 첫째주 토요일 | 10:00 |", "| 매월 첫째주 토요일 | 11:00 |")
    assert inv == {("run_rules", "C001")}


def test_title_change_rechecks_business_match_only(data_dir: Path) -> None:
    inv = invalidated_after(data_dir, "business-explanation.md", "## 급여", "## 급여\n\n## 수수료\n수수료 업무")
    assert ("business_matches", "마감일") in inv
    assert not any(kind == "base_date_rules" and k.startswith("자동이체") for kind, k in inv)


def test_invalidated_values_show_as_unconfirmed(data_dir: Path) -> None:
    store = save_sample(data_dir)
    edit(data_dir, "business-explanation.md", "매월 5일, 10일", "매월 5일, 11일")
    inputs = load_inputs(data_dir)
    result = compute_schedule(inputs, store.load(inputs).confirmed, "2026-11")
    assert Code.BASE_DATE_UNCONFIRMED in result.unconfirmed["B001"]
    assert "A001" not in result.unconfirmed            # 자동이체와 무관한 JOB은 그대로


def test_removed_job_is_ignored(data_dir: Path) -> None:
    store = save_sample(data_dir)
    edit(data_dir, "batch-schedule.md", "| C002 | 아카이빙 구성 | 매월 둘째주 토요일 | 09:00 | |\n", "")
    loaded = store.load(load_inputs(data_dir))
    assert "C002" not in loaded.confirmed.run_rules and ("run_rules", "C002") not in loaded.invalidated


# ------------------------------------------------------------ 깨진 파일, 저장 실패, 임시 파일

@pytest.mark.parametrize("content", ["{not json", "[]", json.dumps({"version": 99}),
                                     json.dumps({"version": 1, "business_matches": {}, "base_date_rules": {"x": 1},
                                                 "run_rules": {}})])
def test_corrupt_file_means_all_unconfirmed(data_dir: Path, content: str) -> None:
    (data_dir / "confirmed.json").write_text(content, encoding="utf-8")
    loaded = ConfirmedStore(data_dir).load(load_inputs(data_dir))
    assert loaded.corrupt and loaded.confirmed.base_date_rules == {} and loaded.confirmed.run_rules == {}


def test_partial_save_failure(data_dir: Path, monkeypatch) -> None:
    store = ConfirmedStore(data_dir)
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise PermissionError("파일이 잠겨 있음")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    failed = store.confirm_many([("run_rules", "C001", ["1째 주 토요일"], "h1"),
                                 ("run_rules", "C002", ["2째 주 토요일"], "h2"),
                                 ("run_rules", "A001", ["첫 영업일"], "h3")])
    assert [(f.code, f.job_id) for f in failed] == [(Code.CONFIRM_SAVE_FAILED, "C002")]
    saved = json.loads((data_dir / "confirmed.json").read_text(encoding="utf-8"))["run_rules"]
    assert set(saved) == {"C001", "A001"}
    assert list(data_dir.glob(".*.tmp")) == []          # 실패해도 임시 파일이 남지 않음


def test_cleanup_leftover_temp_files(data_dir: Path) -> None:
    (data_dir / ".confirmed.json.abc.tmp").write_text("partial", encoding="utf-8")
    assert cleanup_temp_files(data_dir) == 1 and list(data_dir.glob(".*.tmp")) == []


def test_last_writer_wins(data_dir: Path) -> None:
    a, b = ConfirmedStore(data_dir), ConfirmedStore(data_dir)
    a.confirm("run_rules", "C001", ["1째 주 토요일"], "h")
    b.confirm("run_rules", "C001", ["1째 주 일요일"], "h")
    assert ConfirmedStore(data_dir).raw()[0]["run_rules"]["C001"]["value"] == ["1째 주 일요일"]


# ------------------------------------------------------------ 대기 제안

def test_pending_store(data_dir: Path) -> None:
    pending = PendingStore(data_dir)
    proposal = {"value": "매월 5일, 휴일이면 다음 영업일", "evidence": "매월 5일"}
    pending.put("base_date_rule", "자동이체 5일자 응당일", "h1", proposal)
    assert PendingStore(data_dir).get("base_date_rule", "자동이체 5일자 응당일", "h1") == proposal   # 다시 열어도 남음
    assert pending.get("base_date_rule", "자동이체 5일자 응당일", "h2") is None                      # 근거가 바뀌면 재사용 안 함
    pending.put("base_date_rule", "자동이체 5일자 응당일", "h2", proposal)                          # 새 근거로 바꾸면 옛 제안은 지움
    assert len(pending.all()) == 1
    pending.discard("base_date_rule", "자동이체 5일자 응당일")
    assert pending.all() == {}
    assert not (data_dir / "confirmed.json").exists()                                               # 계산용 확정값과 분리


def test_corrupt_pending_starts_empty(data_dir: Path) -> None:
    (data_dir / "pending.json").write_text("{broken", encoding="utf-8")
    assert PendingStore(data_dir).all() == {}
