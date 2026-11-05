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
    ACTION_REFUSED, DATA_CHANGE_REFUSED, OUT_OF_SCOPE, PII_REFUSED, TOO_LONG, RATE_LIMITED_MSG,
)

CASES = ROOT / "evaluation" / "test_queries.csv"
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
    bad = [f"[기대] {t['item']}: {t['reason']}" for t in judgement["traits"] if not t["ok"]]
    bad += [f"[금지] {t['item']}: {t['reason']}" for t in judgement["forbidden"] if not t["ok"]]
    return not bad, bad


def grade(case: dict, answer: str, codes: list[str], tools: list[str], judgement: dict | None) -> dict:
    t_ok, t_missing = token_check(case, answer, codes)
    u_ok, u_note = tool_check(case, answer, tools)
    j_ok, j_bad = judge_check(judgement)
    return {"tokens": {"ok": t_ok, "missing": t_missing}, "tools": {"ok": u_ok, "note": u_note},
            "judge": {"ok": j_ok, "failed": j_bad}, "passed": t_ok and u_ok and j_ok}


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
        j = client.call("eval_judge", payload)
        judgement = j.value.model_dump() if j.ok else None
        res = CaseResult(case["id"], case["category"], case["input"], r["answer"], r["codes"], tools, r["contexts"])
        res.checks = grade(case, r["answer"], r["codes"], tools, judgement)
        res.passed = res.checks["passed"]
        print(f"#{case['id']:>2} {'통과' if res.passed else '실패'} [{case['category']}] {case['input']}")
        out.append(res)
    return out


def run_ragas(results: list[CaseResult], cases: list[dict]) -> dict:
    """근거(contexts)가 있는 답변만 RAGAS로 측정. reference는 expected_traits."""
    import os
    from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    from src.agent import load_settings

    s = load_settings()
    ref = {c["id"]: c["expected_traits"] for c in cases}
    rows = [{"user_input": r.input, "response": r.answer, "retrieved_contexts": [c["text"] for c in r.contexts],
             "reference": ref[r.id]} for r in results if r.contexts]
    llm = LangchainLLMWrapper(ChatBedrockConverse(model=s.model_id, region_name=s.region, max_tokens=4096))
    emb = LangchainEmbeddingsWrapper(BedrockEmbeddings(
        model_id=os.environ.get("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"), region_name=s.region))
    result = evaluate(EvaluationDataset.from_list(rows),
                      metrics=[context_recall, context_precision, faithfulness, answer_relevancy],
                      llm=llm, embeddings=emb, run_config=RunConfig(max_workers=4, timeout=180), show_progress=False)
    df = result.to_pandas()
    scores = {}
    for m in ("context_recall", "context_precision", "faithfulness", "answer_relevancy"):
        vals = [v for v in df[m].tolist() if v is not None and not (isinstance(v, float) and math.isnan(v))]
        scores[m] = round(sum(vals) / len(vals), 3) if vals else None
    scores["rows"] = len(rows)
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
        lines += ["", f"- 측정 건수: {rg.get('rows')}건 (정해진 거절·범위 밖 문장처럼 근거가 없는 답변은 제외)"]
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
    args = ap.parse_args(argv)

    client = make_client()
    if not client.available:
        print("AI 미설정: .env에 Bedrock 자격 증명과 BEDROCK_MODEL_ID가 필요합니다.")
        return 2
    cases = load_cases()
    chat = ChatService(prepare_data(), client, embeddings=bedrock_embeddings())
    results = run_cases(chat, client, cases)
    ragas = None if args.no_ragas else run_ragas(results, cases)
    summary = summarize(results, ragas)
    notes = args.notes.read_text(encoding="utf-8") if args.notes else ""
    report = write_report(args.round, results, summary, notes)
    raw = ROOT / "evaluation" / f"round{args.round}_results.json"
    raw.write_text(json.dumps({"summary": summary, "results": [asdict(r) for r in results]}, ensure_ascii=False,
                              indent=2), encoding="utf-8")
    print(f"\n통과 {summary['passed']}/{summary['total']}, 분류별 {summary['by_category']}, RAGAS {ragas and {k: v for k, v in ragas.items() if k != 'per_case'}}")
    print(f"보고서: {report}")
    return 0 if summary["criteria_met"] or (args.no_ragas and summary["passed"] >= TARGET["pass"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
