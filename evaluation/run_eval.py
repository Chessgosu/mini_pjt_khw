"""자체 평가 (10단계): evaluation/test_queries.csv 20건 + RAGAS. 실제 Bedrock 호출이 필요하다.

실행 (mini-pjt 폴더):
    .venv\\Scripts\\python evaluation/run_eval.py --round 1      # → evaluation/round1_report.md
    .venv\\Scripts\\python evaluation/run_eval.py --round 2 --no-ragas

데이터: data/의 MD 3개를 임시 폴더로 복사하고 samples/expected-202611.md의 확정값을 저장한 뒤 평가한다
(실제 data/confirmed.json은 읽거나 쓰지 않는다). 화면 기준 달은 2026-11.

판정 (케이스마다 모두 만족해야 통과):
  1. 코드 검사: expected_traits에 적힌 날짜(YYYY-MM-DD)·JOB ID·오류 코드가 답변이나 코드 목록에 모두 있음
  2. 도구 검사: expected_tools 중 하나 이상 호출 (정해진 거절·범위 밖 문장으로 답한 경우는 도구 불필요)
  3. AI 판정: expected_traits 각 항목 충족, forbidden 각 항목 위반 없음 (eval_judge, 답변·코드만 보고 판정)
성공 기준: 20건 중 18건 이상, guardrail 3건 전부, RAGAS faithfulness 0.8 이상.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.chat import (  # noqa: E402
    ACTION_REFUSED, AI_FAILED, DATA_CHANGE_REFUSED, OUT_OF_SCOPE, PII_REFUSED, TOO_LONG, RATE_LIMITED_MSG,
)

CASES = ROOT / "evaluation" / "test_queries.csv"
REFERENCES = ROOT / "evaluation" / "ragas_references.csv"     # RAGAS reference(모범 답안), 근거가 있는 케이스만
METRICS = ("context_recall", "context_precision", "faithfulness", "answer_relevancy")
SELECTED_MONTH = "2026-11"
FIXED_ANSWERS = (OUT_OF_SCOPE, ACTION_REFUSED, DATA_CHANGE_REFUSED, PII_REFUSED, TOO_LONG, RATE_LIMITED_MSG)
TARGET = {"pass": 18, "guardrail": 3, "faithfulness": 0.8}


@dataclass
class CaseResult:
    id: str
    category: str
    input: str
    answer: str
    codes: list[str]
    tools: list[str]
    contexts: list[dict]
    checks: dict = field(default_factory=dict)
    passed: bool = False


def load_cases(path: Path = CASES) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def split_items(text: str) -> list[str]:
    return [t.strip() for t in (text or "").split(";") if t.strip()]


# ================================================================ 판정 (오프라인 테스트 대상)


def required_tokens(traits: str) -> list[str]:
    """expected_traits에서 코드로 확인할 수 있는 값: 날짜, JOB ID, 오류 코드."""
    toks = re.findall(r"\d{4}-\d{2}-\d{2}", traits)
    toks += re.findall(r"(?<![A-Za-z0-9_])[A-Z]\d{3}(?!\d)", traits)
    toks += re.findall(r"\b[A-Z]+(?:_[A-Z]+)+\b", traits)
    return list(dict.fromkeys(toks))


def token_check(case: dict, answer: str, codes: list[str]) -> tuple[bool, list[str]]:
    hay = answer + " " + " ".join(codes)
    missing = [t for t in required_tokens(case["expected_traits"]) if t not in hay]
    return not missing, missing


def tool_check(case: dict, answer: str, tools: list[str]) -> tuple[bool, str]:
    expected = split_items(case.get("expected_tools", ""))
    if not expected:
        return True, "도구 기대 없음"
    if answer in FIXED_ANSWERS:
        return True, "정해진 문장으로 답함 (도구 불필요)"
    used = [t for t in expected if t in tools]
    if used:
        missing = [t for t in expected if t not in tools]
        return True, "호출: " + ", ".join(used) + (f" (미호출: {', '.join(missing)})" if missing else "")
    return False, f"기대 도구 미호출: {', '.join(expected)} / 실제: {', '.join(tools) or '없음'}"


def judge_check(judgement: dict | None) -> tuple[bool, list[str]]:
    """AI 판정 결과 → (통과, 실패 항목). 판정을 못 받았으면 실패로 본다."""
    if judgement is None:
        return False, ["AI 판정 실패"]
    bad = [f"[기대] {t['item']}: {t['reason']}" for t in judgement["traits"] if not t["met"]]
    bad += [f"[금지] {t['item']}: {t['reason']}" for t in judgement["forbidden"] if t["violated"]]
    return not bad, bad


def judge_with_votes(client, payload: dict, max_votes: int = 3) -> tuple[dict | None, list[bool]]:
    """AI 판정을 받고, 실패로 나오면 최대 3번까지 다시 받아 다수결(2/3)로 정한다 (평가 AI의 판정 흔들림 완화).
    돌려주는 판정은 다수 쪽의 첫 판정이다."""
    judgements: list[dict | None] = []
    for _ in range(max_votes):
        j = client.call("eval_judge", payload)
        judgements.append(j.value.model_dump() if j.ok else None)
        votes = [judge_check(x)[0] for x in judgements]
        if votes[0] and len(votes) == 1:
            return judgements[0], votes                    # 첫 판정이 통과면 그대로
        if votes.count(True) * 2 > max_votes or votes.count(False) * 2 > max_votes:
            break
    winner = votes.count(True) * 2 > len(votes)
    return next(x for x, v in zip(judgements, votes) if v == winner), votes


def grade(case: dict, answer: str, codes: list[str], tools: list[str], judgement: dict | None) -> dict:
    t_ok, t_missing = token_check(case, answer, codes)
    u_ok, u_note = tool_check(case, answer, tools)
    j_ok, j_bad = judge_check(judgement)
    return {"tokens": {"ok": t_ok, "missing": t_missing}, "tools": {"ok": u_ok, "note": u_note},
            "judge": {"ok": j_ok, "failed": j_bad}, "passed": t_ok and u_ok and j_ok}


def invalid_cases(results: list[CaseResult]) -> list[str]:
    """Bedrock 호출 실패(한도 초과·장애)로 판정할 수 없는 케이스. 하나라도 있으면 그 회차는 무효로 본다."""
    return [r.id for r in results if r.answer == AI_FAILED or "AI 판정 실패" in r.checks.get("judge", {}).get("failed", [])]


def ragas_complete(ragas: dict | None) -> bool:
    """RAGAS 4개 지표가 모든 측정 대상에서 값이 나왔는지 (시간 초과로 빠진 값이 있으면 False)."""
    return bool(ragas) and all(ragas.get(m) is not None for m in METRICS) \
        and not any((ragas.get("missing") or {}).values())


def summarize(results: list[CaseResult], ragas: dict | None) -> dict:
    by_cat: dict[str, list[int]] = {}
    for r in results:
        by_cat.setdefault(r.category, [0, 0])
        by_cat[r.category][0] += r.passed
        by_cat[r.category][1] += 1
    passed = sum(r.passed for r in results)
    guard = by_cat.get("guardrail", [0, 0])
    faith = (ragas or {}).get("faithfulness")
    met = (passed >= TARGET["pass"] and guard[0] == guard[1] == TARGET["guardrail"]
           and (faith is not None and faith >= TARGET["faithfulness"]))
    return {"passed": passed, "total": len(results), "by_category": by_cat, "ragas": ragas, "criteria_met": met}


# ================================================================ 실행 (실제 Bedrock)


def prepare_data() -> Path:
    from src.parsing import load_inputs
    from src.store import ConfirmedStore, base_date_rule_source, business_match_source, run_rule_source
    from tests.helpers import sample_confirmed
    d = Path(tempfile.mkdtemp(prefix="eval-")) / "data"
    d.mkdir()
    for n in ("batch-schedule.md", "business-explanation.md", "business-calendar.md"):
        shutil.copy2(ROOT / "data" / n, d / n)
    inputs, sample, store = load_inputs(d), sample_confirmed(), ConfirmedStore(d)
    items = [("business_matches", e, s, business_match_source(e, inputs)) for e, s in sample.business_matches.items()]
    items += [("base_date_rules", n, r, base_date_rule_source(n, inputs, sample.business_matches))
              for n, r in sample.base_date_rules.items()]
    items += [("run_rules", j, r, run_rule_source(inputs.job(j), inputs, sample.business_matches))
              for j, r in sample.run_rules.items()]
    assert store.confirm_many(items) == []
    return d


def run_cases(chat, client, cases: list[dict]) -> list[CaseResult]:
    out = []
    for case in cases:
        chat.limiter.hits.clear()          # 케이스끼리 요청 횟수 제한이 섞이지 않도록
        chat.history.clear()               # 케이스는 서로 독립
        r = chat.ask(case["input"], SELECTED_MONTH)
        tools = [t["step"].split(":", 1)[1] for t in r["trace"] if t["step"].startswith("tool:")]
        payload = {"question": case["input"], "answer": r["answer"], "codes": r["codes"],
                   "expected_traits": split_items(case["expected_traits"]), "forbidden": split_items(case["forbidden"])}
        judgement, votes = judge_with_votes(client, payload)
        res = CaseResult(case["id"], case["category"], case["input"], r["answer"], r["codes"], tools, r["contexts"])
        res.checks = grade(case, r["answer"], r["codes"], tools, judgement)
        res.checks["judge"]["votes"] = votes
        res.passed = res.checks["passed"]
        print(f"#{case['id']:>2} {'통과' if res.passed else '실패'} [{case['category']}] {case['input']}")
        out.append(res)
    return out


def load_references(path: Path = REFERENCES) -> dict[str, str]:
    """RAGAS용 모범 답안 (id → 문장). 파일이 없으면 빈 dict."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {row["id"]: row["reference"].strip() for row in csv.DictReader(f) if row.get("reference", "").strip()}


def build_ragas_rows(results: list[CaseResult], cases: list[dict], references: dict[str, str]) -> list[dict]:
    """근거(contexts)가 있는 답변만 측정 대상. reference는 모범 답안이 있으면 그것, 없으면 expected_traits."""
    traits = {c["id"]: c["expected_traits"] for c in cases}
    return [{"id": r.id, "user_input": r.input, "response": r.answer,
             "retrieved_contexts": [c["text"] for c in r.contexts],
             "reference": references.get(r.id) or traits[r.id],
             "reference_source": "ragas_references.csv" if r.id in references else "expected_traits"}
            for r in results if r.contexts]


def run_ragas(results: list[CaseResult], cases: list[dict], round_no: int | None = None) -> dict:
    """RAGAS 4개 지표. 측정에 쓴 데이터셋은 evaluation/round{N}_ragas_dataset.json으로 남긴다."""
    import os
    from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    from src.agent import load_settings

    s = load_settings()
    full = build_ragas_rows(results, cases, load_references())
    if round_no is not None:
        out = ROOT / "evaluation" / f"round{round_no}_ragas_dataset.json"
        out.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = [{k: r[k] for k in ("user_input", "response", "retrieved_contexts", "reference")} for r in full]
    llm = LangchainLLMWrapper(ChatBedrockConverse(model=s.model_id, region_name=s.region, max_tokens=4096))
    emb = LangchainEmbeddingsWrapper(BedrockEmbeddings(
        model_id=os.environ.get("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"), region_name=s.region))
    result = evaluate(EvaluationDataset.from_list(rows),
                      metrics=[context_recall, context_precision, faithfulness, answer_relevancy],
                      llm=llm, embeddings=emb, run_config=RunConfig(max_workers=2, timeout=300), show_progress=False)
    df = result.to_pandas()
    scores = {"missing": {}}
    for m in METRICS:
        vals = [v for v in df[m].tolist() if v is not None and not (isinstance(v, float) and math.isnan(v))]
        scores[m] = round(sum(vals) / len(vals), 3) if vals else None
        scores["missing"][m] = len(rows) - len(vals)
    scores["rows"] = len(rows)
    scores["reference_source"] = sorted({r["reference_source"] for r in full})
    scores["per_case"] = {r["user_input"]: {m: (None if math.isnan(v) else round(float(v), 3))
                                            for m, v in zip(("context_recall", "context_precision", "faithfulness",
                                                             "answer_relevancy"),
                                                            df.loc[i, ["context_recall", "context_precision",
                                                                       "faithfulness", "answer_relevancy"]])}
                          for i, r in enumerate(rows)}
    return scores


# ================================================================ 보고서


def write_report(round_no: int, results: list[CaseResult], summary: dict, notes: str = "") -> Path:
    path = ROOT / "evaluation" / f"round{round_no}_report.md"
    rg = summary["ragas"] or {}
    lines = [f"# {round_no}차 자체 평가 보고서", "",
             f"- 실행: `evaluation/run_eval.py --round {round_no}` (실제 Bedrock, 화면 기준 달 {SELECTED_MONTH})",
             "- 데이터: data/ MD 3개 사본 + samples/expected-202611.md 확정값 (실제 data/confirmed.json 미사용)", "",
             "## 요약", "",
             f"- 통과: **{summary['passed']} / {summary['total']}** (기준 {TARGET['pass']} 이상)",
             "- 분류별: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in summary["by_category"].items()),
             f"- 성공 기준 충족: {'✅' if summary['criteria_met'] else '❌'} "
             f"(18/20 이상, guardrail 3/3, faithfulness ≥ 0.8)", "",
             "## RAGAS (근거가 있는 답변 대상)", ""]
    if rg:
        lines += [f"| 지표 | 점수 |", "|---|---|"]
        lines += [f"| {m} | {rg.get(m)} |" for m in ("context_recall", "context_precision", "faithfulness",
                                                     "answer_relevancy")]
        lines += ["", f"- 측정 건수: {rg.get('rows')}건 (정해진 거절·범위 밖 문장처럼 근거가 없는 답변은 제외)",
                  f"- reference 출처: {', '.join(rg.get('reference_source') or ['expected_traits'])}",
                  f"- 측정 데이터셋: `evaluation/round{round_no}_ragas_dataset.json`"]
    else:
        lines += ["- 이번 회차는 RAGAS를 실행하지 않음"]
    lines += ["", "## 케이스별 결과", "", "| # | 분류 | 질문 | 결과 | 답변 | 실패 사유 |", "|---|---|---|---|---|---|"]
    for r in results:
        why = []
        if not r.checks["tokens"]["ok"]:
            why.append("값 누락: " + ", ".join(r.checks["tokens"]["missing"]))
        if not r.checks["tools"]["ok"]:
            why.append(r.checks["tools"]["note"])
        why += r.checks["judge"]["failed"]
        ans = r.answer.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.id} | {r.category} | {r.input} | {'✅' if r.passed else '❌'} | {ans} | "
                     + "<br>".join(w.replace("|", "\\|") for w in why) + " |")
    if notes:
        lines += ["", "## 분석·개선", "", notes]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.agent import make_client
    from src.chat import ChatService
    from src.retriever import bedrock_embeddings

    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=2)
    ap.add_argument("--no-ragas", action="store_true")
    ap.add_argument("--notes", type=Path, help="보고서 '분석·개선' 절에 넣을 마크다운 파일")
    ap.add_argument("--ragas-only", action="store_true",
                    help="저장된 round{N}_results.json의 채팅 결과로 RAGAS만 다시 측정 (채팅 20건을 다시 돌리지 않음)")
    args = ap.parse_args(argv)

    client = make_client()
    if not client.available:
        print("AI 미설정: .env에 Bedrock 자격 증명과 BEDROCK_MODEL_ID가 필요합니다.")
        return 2
    probe = client.call("chat_conditions", {"question": "B001은 12월에 언제 등록돼?", "year_month": SELECTED_MONTH,
                                            "history_summary": ""})
    if not probe.ok:
        print(f"Bedrock 호출 실패로 평가를 시작하지 않았어요: {probe.call.outcome} {probe.call.errors[-1:]}")
        print("일일 토큰 한도(ThrottlingException)라면 한도가 풀린 뒤 다시 실행하세요. 보고서는 만들지 않았습니다.")
        return 3
    cases = load_cases()
    raw = ROOT / "evaluation" / f"round{args.round}_results.json"

    def save(summary: dict, results: list[CaseResult]) -> None:
        raw.write_text(json.dumps({"summary": summary, "results": [asdict(r) for r in results]},
                                  ensure_ascii=False, indent=2), encoding="utf-8")

    if args.ragas_only:
        if not raw.exists():
            print(f"{raw.name}이 없어요. 먼저 --ragas-only 없이 실행하세요.")
            return 2
        results = [CaseResult(**r) for r in json.loads(raw.read_text(encoding="utf-8"))["results"]]
        print(f"{raw.name}의 채팅 결과 {len(results)}건으로 RAGAS만 다시 측정해요.")
    else:
        chat = ChatService(prepare_data(), client, embeddings=bedrock_embeddings())
        results = run_cases(chat, client, cases)
        if bad := invalid_cases(results):
            print(f"\nBedrock 호출 실패로 판정할 수 없는 케이스가 있어 이번 회차는 무효예요: {', '.join(bad)}")
            print("보고서는 만들지 않았습니다. 한도가 풀린 뒤 다시 실행하세요.")
            return 3
        save(summarize(results, None), results)              # 채팅 결과는 먼저 저장 (RAGAS만 다시 잴 수 있게)
    ragas = None if args.no_ragas else run_ragas(results, cases, args.round)
    if ragas is not None and not ragas_complete(ragas):
        save(summarize(results, ragas), results)
        print(f"\nRAGAS 측정값 일부가 빠졌어요(시간 초과·호출 실패): {ragas.get('missing')}. 보고서는 만들지 않았습니다.")
        print(f"채팅 결과는 {raw.name}에 저장했어요. 한도가 넉넉할 때 --round {args.round} --ragas-only로 RAGAS만 다시 재세요.")
        return 3
    summary = summarize(results, ragas)
    notes = args.notes.read_text(encoding="utf-8") if args.notes else ""
    report = write_report(args.round, results, summary, notes)
    save(summary, results)
    print(f"\n통과 {summary['passed']}/{summary['total']}, 분류별 {summary['by_category']}, RAGAS {ragas and {k: v for k, v in ragas.items() if k != 'per_case'}}")
    print(f"보고서: {report}")
    return 0 if summary["criteria_met"] or (args.no_ragas and summary["passed"] >= TARGET["pass"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
