"""06단계 RAG: 절 단위 분할, BM25(kiwipiepy)·벡터(Chroma)·제목 일치 하이브리드, 근거 문장 검사, 도구."""
import hashlib
import math
from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings

from src.parsing import load_inputs, parse_explanation
from src.retriever import (
    DOC_PREFIX, EVIDENCE_MISMATCH, BusinessRetriever, check_evidence, evidence_in_source, make_retrieve_tool,
    tokenize,
)


class FakeEmbeddings(Embeddings):
    """네트워크 없는 결정적 임베딩: 한글 글자 2-gram 해시 → 256차원 단위 벡터."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.seen: list[str] = []

    def _vec(self, text: str) -> list[float]:
        self.seen.append(text)
        if self.fail:
            raise RuntimeError("임베딩 호출 실패")
        v = [0.0] * 256
        for w in text.split():
            for i in range(len(w) - 1):
                v[int(hashlib.md5(w[i:i + 2].encode()).hexdigest(), 16) % 256] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture(params=["bm25", "hybrid"])
def retriever(request, data_dir: Path) -> BusinessRetriever:
    emb = FakeEmbeddings() if request.param == "hybrid" else None
    r = BusinessRetriever(load_inputs(data_dir).explanation, emb)
    assert r.mode == request.param
    return r


# ------------------------------------------------------------ 분할

def test_one_document_per_section(data_dir: Path) -> None:
    r = BusinessRetriever(load_inputs(data_dir).explanation)
    assert r.ids == [DOC_PREFIX + n for n in ("자동이체", "급여", "마감", "결산")]
    assert r.docs[2].startswith("## 마감\n") and "첫 영업일" in r.docs[2]
    assert "## 급여" not in r.docs[0]                                   # 절끼리 섞이지 않음


# ------------------------------------------------------------ 완료 기준 + 대표 질의

@pytest.mark.parametrize("query,section", [
    ("마감일", "마감"),
    ("자동이체 응당일", "자동이체"),
    ("자동이체 5일자 응당일", "자동이체"),
    ("결산 기준일", "결산"),
    ("급여 지급일", "급여"),
    ("출금일자가 주말이면?", "자동이체"),
    ("응당일이 겹치면 어떻게 돼?", "자동이체"),
    ("전월 금액 대사", "마감"),
    ("입금반영일", "자동이체"),
    ("임직원 월급", "급여"),
])
def test_top1_section(retriever: BusinessRetriever, query: str, section: str) -> None:
    assert retriever.search(query)[0].section == section


def test_closing_day_not_confused_with_settlement(retriever: BusinessRetriever) -> None:
    """결산 절 본문에 '마감일'이 나오지만 '마감일' 질의의 1순위는 마감 절."""
    top = retriever.search("마감일", k=4)
    assert [d.section for d in top][:2] == ["마감", "결산"]
    assert top[0].score > top[1].score                                  # 동점이 아님


def test_result_fields(retriever: BusinessRetriever) -> None:
    d = retriever.search("자동이체 응당일", k=2)[0]
    assert d.doc_id == "business-explanation#자동이체" and d.text.startswith("## 자동이체")
    assert d.ranks["title"] == 1 and d.ranks["bm25"] == 1
    assert len(retriever.search("자동이체 응당일", k=2)) <= 2


def test_unrelated_query_returns_few_or_none(data_dir: Path) -> None:
    r = BusinessRetriever(load_inputs(data_dir).explanation)
    assert r.search("오늘 날씨 어때?") == []


def test_tokenize_morphemes_and_bigrams() -> None:
    toks = tokenize("마감후속 작업")
    assert "작업" in toks and "마감" in toks and "후속" in toks


@pytest.mark.parametrize("query,section", [("마감후속", "결산"), ("기준일은", "결산")])
def test_bm25_handles_spacing_and_particles(data_dir: Path, query: str, section: str) -> None:
    """띄어쓰기 없는 질의·조사 붙은 질의도 BM25 순위 1위가 맞는 절이다 (글자 2-gram)."""
    r = BusinessRetriever(load_inputs(data_dir).explanation)
    assert r.sections[r._bm25_ranking(query)[0]].name == section


# ------------------------------------------------------------ 임베딩 실패·마스킹

def test_embedding_failure_falls_back_to_bm25(data_dir: Path) -> None:
    r = BusinessRetriever(load_inputs(data_dir).explanation, FakeEmbeddings(fail=True))
    assert r.mode == "bm25" and r.vector_error
    assert r.search("마감일")[0].section == "마감"


def test_text_masked_before_embedding() -> None:
    text = "## 자동이체\n문의 010-1234-5678, 계좌 110-123-456789\n\n## 마감\n첫 영업일"
    emb = FakeEmbeddings()
    r = BusinessRetriever(parse_explanation(text)[0], emb)
    r.search("자동이체 010-1111-2222 문의")
    seen = " ".join(emb.seen)
    assert "010-1234-5678" not in seen and "110-123-456789" not in seen and "010-1111-2222" not in seen
    assert "010-1234-5678" in r.docs[0]                                 # 화면용 원문은 그대로


def test_empty_explanation() -> None:
    r = BusinessRetriever(parse_explanation("# 제목만\n")[0], FakeEmbeddings())
    assert r.search("마감일") == []


def test_rebuild_after_edit(data_dir: Path) -> None:
    """MD를 고친 뒤 다시 만들면 새 절이 검색된다 (인덱스는 입력에서 매번 만든다)."""
    p = data_dir / "business-explanation.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n## 수수료\n수수료는 매월 말일에 청구합니다.\n", encoding="utf-8")
    r = BusinessRetriever(load_inputs(data_dir).explanation, FakeEmbeddings())
    assert r.search("수수료 청구일")[0].section == "수수료"


# ------------------------------------------------------------ 근거 문장 검사

def test_evidence_whitespace_insensitive(data_dir: Path) -> None:
    src = load_inputs(data_dir).explanation.sections["마감"].text
    assert evidence_in_source("매월 첫 영업일에   진행하며,\n첫 영업일이 응당일인 경우", src)
    assert check_evidence("매월 첫 영업일에 진행하며", [src]) is None


@pytest.mark.parametrize("evidence", ["매월 둘째 영업일에 진행하며", "", "   ", "마감은 매월 1일입니다"])
def test_evidence_mismatch(data_dir: Path, evidence: str) -> None:
    src = load_inputs(data_dir).explanation.sections["마감"].text
    assert check_evidence(evidence, [src]) == EVIDENCE_MISMATCH


# ------------------------------------------------------------ 도구

def test_retrieve_tool(data_dir: Path) -> None:
    tool = make_retrieve_tool(BusinessRetriever(load_inputs(data_dir).explanation))
    assert tool.name == "retrieve_business_docs" and tool.description
    out = tool.invoke({"query": "마감일", "k": 2})
    assert out[0]["section"] == "마감" and out[0]["doc_id"] == "business-explanation#마감" and len(out) <= 2
