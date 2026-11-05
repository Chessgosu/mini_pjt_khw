"""09단계 ac_same_dates_everywhere: 매트릭스·xlsx 검증 기준·채팅 답변의 등록일이 같다 (같은 엔진 결과)."""
from pathlib import Path

from src.views import jobs_on_day, load_state, schedule_matrix
from src.verify import verify_upload
from tests.chat_helpers import make_chat

CORRECT = Path(__file__).resolve().parent.parent / "samples" / "correct" / "BATCH_SCHEDULE_202611.xlsx"


def verified_marked_ok(data_dir: Path, day: int) -> set[str]:
    state = load_state(data_dir, "2026-11")
    outcome = verify_upload(data_dir, CORRECT.read_bytes(), CORRECT.name, state.confirmed)
    marks = outcome.validation.uploaded.rows
    return {v.job_id for v in outcome.judged.verdicts
            if v.day.day == day and v.status == "ok" and day in marks.get(v.job_id, set())}


def chat_jobs(chat, question: str) -> set[str]:
    r = chat.ask(question, "2026-11")
    return {c["doc_id"].split("#")[2] for c in r["contexts"]}, r["answer"]


def test_25th_same_everywhere(data_dir: Path, fake_llm) -> None:
    chat = make_chat(data_dir, fake_llm)                                      # 샘플 확정값 저장 포함
    matrix = set(jobs_on_day(schedule_matrix(load_state(data_dir, "2026-11")), 25))
    verified = verified_marked_ok(data_dir, 25)
    cited, answer = chat_jobs(chat, "25일에 등록되는 배치는 뭐야?")
    assert matrix == verified == cited == {"B106", "B201", "D001", "E001"}
    assert all(j in answer for j in matrix)


def test_b006_same_everywhere(data_dir: Path, fake_llm) -> None:
    chat = make_chat(data_dir, fake_llm)
    m = schedule_matrix(load_state(data_dir, "2026-11"))
    row = next(r for r in m["rows"] if r["job_id"] == "B006")
    matrix_days = [d + 1 for d, c in enumerate(row["cells"]) if c]
    verified_days = [d for d in range(1, 31) if "B006" in verified_marked_ok(data_dir, d)]
    r = chat.ask("B006은 11월에 언제 등록돼?", "2026-11")
    chat_days = sorted(int(c["doc_id"].split("#")[1][-2:]) for c in r["contexts"])
    assert matrix_days == verified_days == chat_days == [1, 29]


def test_confirm_change_reflected_everywhere(data_dir: Path, fake_llm) -> None:
    """확정값을 바꾸면(5일자 휴일 보정 → 이전 영업일) 세 곳이 함께 바뀐다."""
    from src.parsing import load_inputs
    from src.store import ConfirmedStore, base_date_rule_source
    chat = make_chat(data_dir, fake_llm)
    inputs = load_inputs(data_dir)
    store = ConfirmedStore(data_dir)
    rule = "매월 5일, 휴일이면 이전 영업일, 앞선 자동이체 응당일과 겹치면 수행 안 함"
    name = "자동이체 5일자 응당일"
    store.confirm("base_date_rules", name, rule,
                  base_date_rule_source(name, inputs, store.load(inputs).confirmed.business_matches))
    m = schedule_matrix(load_state(data_dir, "2026-12"))
    row = next(r for r in m["rows"] if r["job_id"] == "B001")
    assert [d + 1 for d, c in enumerate(row["cells"]) if c] == [3]          # 12-05(토) → 12-04 수행, 12-03 등록
    r = chat.ask("B001은 12월에 언제 등록돼?", "2026-11")
    assert "2026-12-03" in r["answer"]
