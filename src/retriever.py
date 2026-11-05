"""업무 설명 검색 (06단계, RAG 하이브리드).

- 문서 단위: business-explanation.md의 "## 업무명" 절 하나 = 문서 하나 (doc_id "business-explanation#<업무명>")
- BM25: kiwipiepy 형태소 + 어절 글자 2-gram
- 벡터: Chroma(메모리) + Bedrock 임베딩. 임베딩을 쓸 수 없으면 BM25·제목 일치만으로 검색 (AI 선택 기능)
- 제목 일치: 질의에 절 이름이 들어 있으면 그 절
- 결합: 세 순위를 RRF(Reciprocal Rank Fusion, k=60)로 합친다 (제목 일치는 가중치 2)
- 근거 문장 검사: 공백 정규화 후 원문 포함 여부. 없으면 "근거 불일치"

임베딩으로 보내는 MD 원문과 질의는 agent.mask로 개인정보·자격 증명을 가린 뒤 보낸다.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from rank_bm25 import BM25Okapi

from src.agent import mask
from src.parsing import Explanation, normalize_text

log = logging.getLogger("batch.ai")

DOC_PREFIX = "business-explanation#"
RRF_K = 60
RRF_WEIGHTS = {"bm25": 1.0, "vector": 1.0, "title": 2.0}   # 절 이름이 질의에 그대로 있으면 가장 강한 신호
EVIDENCE_MISMATCH = "근거 불일치"
CONTENT_TAGS = ("NNG", "NNP", "NR", "SL", "SN", "SH", "XR", "VV", "VA", "MAG")


# ================================================================ 토큰화


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi
    return Kiwi()


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for t in _kiwi().tokenize(text):
        if t.tag in CONTENT_TAGS:
            tokens.append(t.form)
    for word in re.findall(r"[가-힣]+", text):              # 띄어쓰기·형태소 분석 차이 보완 (마감후속, 기준일은)
        tokens += [word[i:i + 2] for i in range(len(word) - 1)]
    return tokens


# ================================================================ 결과


@dataclass(frozen=True)
class RetrievedDoc:
    doc_id: str
    section: str
    text: str
    score: float
    ranks: dict = field(default_factory=dict, compare=False, hash=False)   # 방식별 순위 (bm25, vector, title)


def evidence_in_source(evidence: str, source: str) -> bool:
    """AI가 낸 근거 문장이 원문에 (공백 차이 무시하고) 그대로 들어 있는지."""
    ev = normalize_text(evidence)
    return bool(ev) and ev in normalize_text(source)


def check_evidence(evidence: str, sources: list[str]) -> str | None:
    """원문 어디에도 없으면 '근거 불일치'를 돌려준다."""
    return None if any(evidence_in_source(evidence, s) for s in sources) else EVIDENCE_MISMATCH


# ================================================================ 검색기


class BusinessRetriever:
    def __init__(self, explanation: Explanation, embeddings: Any | None = None) -> None:
        self.sections = list(explanation.sections.values())
        self.docs = [f"## {s.name}\n{s.text}" for s in self.sections]
        self.ids = [DOC_PREFIX + s.name for s in self.sections]
        self._bm25 = BM25Okapi([tokenize(d) for d in self.docs]) if self.docs else None
        self.vector_error: str | None = None
        self._store = None
        if embeddings is not None and self.docs:
            self._build_vector(embeddings)

    @property
    def mode(self) -> str:
        return "hybrid" if self._store is not None else "bm25"

    def _build_vector(self, embeddings: Any) -> None:
        try:
            import chromadb
            from langchain_chroma import Chroma
            self._store = Chroma(collection_name=f"biz-{uuid.uuid4().hex}", embedding_function=embeddings,
                                 client=chromadb.EphemeralClient(), collection_metadata={"hnsw:space": "cosine"})
            self._store.add_texts(texts=[mask(d) for d in self.docs], ids=self.ids,
                                  metadatas=[{"section": s.name} for s in self.sections])
        except Exception as exc:  # noqa: BLE001 — 임베딩 실패 시 BM25로 계속
            self._store = None
            self.vector_error = mask(f"{type(exc).__name__}: {exc}")
            log.warning("벡터 색인 실패, BM25로 검색: %s", self.vector_error)

    # ---------- 순위 목록

    def _bm25_ranking(self, query: str) -> list[int]:
        q = tokenize(query)
        if not q or self._bm25 is None:
            return []
        scores = self._bm25.get_scores(q)
        order = sorted(range(len(self.docs)), key=lambda i: (-scores[i], i))
        return [i for i in order if scores[i] > 0]

    def _vector_ranking(self, query: str, k: int) -> list[int]:
        if self._store is None:
            return []
        try:
            hits = self._store.similarity_search(mask(query), k=min(k, len(self.docs)))
        except Exception as exc:  # noqa: BLE001
            log.warning("벡터 검색 실패, BM25로 검색: %s", mask(str(exc)))
            return []
        index = {s.name: i for i, s in enumerate(self.sections)}
        return [index[h.metadata["section"]] for h in hits if h.metadata.get("section") in index]

    def _title_ranking(self, query: str) -> list[int]:
        q = normalize_text(query)
        hits = [i for i, s in enumerate(self.sections) if s.name in q]
        return sorted(hits, key=lambda i: -len(self.sections[i].name))

    # ---------- 하이브리드

    def search(self, query: str, k: int = 3) -> list[RetrievedDoc]:
        rankings = {"bm25": self._bm25_ranking(query),
                    "vector": self._vector_ranking(query, len(self.docs)),
                    "title": self._title_ranking(query)}
        scores: dict[int, float] = {}
        ranks: dict[int, dict] = {}
        for name, order in rankings.items():
            for r, i in enumerate(order, start=1):
                scores[i] = scores.get(i, 0.0) + RRF_WEIGHTS[name] / (RRF_K + r)
                ranks.setdefault(i, {})[name] = r
        best = sorted(scores, key=lambda i: (-scores[i], i))[:k]
        return [RetrievedDoc(self.ids[i], self.sections[i].name, self.docs[i], round(scores[i], 6), ranks[i])
                for i in best]


def bedrock_embeddings(settings=None):
    """설정이 있으면 Bedrock 임베딩, 없으면 None (BM25로 동작)."""
    import os
    from src.agent import load_settings
    settings = settings or load_settings()
    if not settings.configured:
        return None
    from langchain_aws import BedrockEmbeddings
    return BedrockEmbeddings(model_id=os.environ.get("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"),
                             region_name=settings.region)


# ================================================================ 도구


def make_retrieve_tool(retriever: BusinessRetriever):
    """에이전트 도구 retrieve_business_docs. 결과는 업무 설명 원문 절 (데이터로만 쓴다)."""
    from langchain_core.tools import tool

    @tool("retrieve_business_docs")
    def retrieve_business_docs(query: str, k: int = 3) -> list[dict]:
        """업무 설명 문서(business-explanation.md)에서 질의와 관련된 '## 업무' 절을 찾아 원문을 돌려준다."""
        return [{"doc_id": d.doc_id, "section": d.section, "text": d.text, "score": d.score}
                for d in retriever.search(query, k)]

    return retrieve_business_docs
