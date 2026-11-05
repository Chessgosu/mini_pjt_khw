# 미니 PJT: 월 배치 등록 일정 확인·검증 에이전트

## 무엇을 푸나
업무 기준과 휴일에 따라 매달 달라지는 배치 작업 등록일을 정확히 계산하고, 외부 사이트 등록 내역의 누락·초과를 찾아 주며, 일정을 문장으로 물어보면 답하는 에이전트입니다.

## 활용한 패턴 (Day 1~7)
> 개발 전 계획입니다. 구현 후 실제 적용 내용으로 갱신합니다.

- Day 1: LCEL chain (Pydantic 구조화 출력) — 기준일 제안·업무 매칭·규칙 정규화·채팅 질문 조건·답변 인용 목록을 모두 Pydantic 모델로 받아 스키마 검증
- Day 2: RAG (하이브리드 검색) — `business-explanation.md`를 업무 절 단위로 나눠 BM25(kiwipiepy 한국어 형태소) + 벡터 검색(Chroma)으로 기준일 판단 근거를 찾음
- Day 3: ReAct (도구 자율 선택) — 채팅 에이전트가 `get_registration_schedule`, `find_jobs_by_date`, `retrieve_business_docs`를 질문에 맞게 선택
- Day 4: 도구 다중 — 한 질문에 일정 조회 + 업무 문서 검색을 함께 사용 (예: "11월 마감일은 언제야?")
- Day 5: 가드레일 — 프롬프트 인젝션 방어, 답변 날짜·JOB ID를 계산 결과와 대조하는 출력 검사
- Day 5: HITL — AI가 제안한 기준일·규칙·업무 매칭을 `interrupt()`로 사용자 확인 후 반영
- Day 5: 미들웨어 — AI 호출 재시도(1회)·시간 제한(30초), 채팅 이력 요약, 로그의 자격 증명 마스킹
- Day 7: Observability — LangSmith 트레이스 기록
- Day 7: 평가 — RAGAS + 인-아웃 세트(`evaluation/test_queries.csv`) 자체 평가

## 아키텍처

```
[Streamlit 화면] ── 등록 일정 매트릭스 / 기준일·규칙 확인 / xlsx 검증 / 채팅
       │
       ▼
[src/agent.py]  LangGraph 에이전트
   ├─ 확인 그래프: 업무 매칭 → 기준일 제안 → 규칙 정규화 → interrupt()(사용자 확인) → confirmed.json
   └─ 채팅 그래프: 질문 → 조건 풀기(Pydantic) → 도구 호출(ReAct) → 답변 + 인용 목록 → 정답 목록과 대조
       │
       ├─▶ [src/tools.py]      계산 엔진(영업일·D±N·등록일) · 일정 조회 · xlsx 가드레일·판정
       └─▶ [src/retriever.py]  업무 설명 하이브리드 검색 (BM25 + Chroma)
                │
                ▼
   data/ (batch-schedule.md · business-explanation.md · business-calendar.md · confirmed.json)
```

- 날짜는 **계산 엔진(코드)만** 정하고, AI는 제안과 문장화만 맡습니다.
- AI(Claude, AWS Bedrock)가 없어도 기준일·규칙을 직접 입력하면 일정 계산·검증은 동작합니다.

## 실행 방법

```bash
pip install -r requirements.txt
cp .env.example .env        # AWS 리전·자격 증명·모델 ID 설정
streamlit run src/app.py
```

- 테스트: `python -m pytest -q`
- 샘플 데이터: `data/` (입력), `samples/` (검증용 xlsx와 정답 `expected-202611.md`)

## RAGAS 평가 결과
- context_recall: (평가 후 기입)
- context_precision: (평가 후 기입)
- faithfulness: (평가 후 기입)
- answer_relevancy: (평가 후 기입)

## 인-아웃 세트 통과율 (자체 평가)
- 1차 (Day 9 종료): XX / 20 통과
- 2차 (Day 10 개선 후): XX / 20 통과
- 개선폭: +XX 건 (주요 개선 사항: ...)

## 트라이앤에러 회고
- 시도했지만 실패한 접근 · 왜 실패했는가: (개발 후 기입)
- 최종 채택한 접근 · 왜 그것으로 갔는가: (개발 후 기입)
- 남은 한계 · 향후 개선 방향: (개발 후 기입)

## 핵심 코드 위치
- `src/agent.py:NN` — 메인 에이전트 그래프 (확인 그래프 · 채팅 그래프)
- `src/tools.py` — 도메인 도구 (계산 엔진 · 일정 조회 · xlsx 검증)
- `src/retriever.py` — RAG 파이프라인 (업무 설명 하이브리드 검색)
- `src/app.py` — Streamlit 화면
