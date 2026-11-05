# 미니 PJT: 월 배치 등록 일정 확인·검증 에이전트

## 무엇을 푸나
업무 기준과 휴일에 따라 매달 달라지는 배치 작업 등록일을 정확히 계산하고, 외부 사이트 등록 내역(xlsx)의 누락·초과를 찾아 주며, 일정을 문장으로 물어보면 계산 결과만 근거로 답하는 에이전트입니다.

- 입력: `data/batch-schedule.md`(JOB별 수행일 규칙·시작 시각), `data/business-explanation.md`(업무 기준 설명), `data/business-calendar.md`(공휴일)
- 날짜는 **계산 엔진(코드)만** 정하고, AI는 규칙 해석 제안과 문장화만 맡습니다. AI 제안은 사용자가 확인해야 계산에 쓰입니다.

## 활용한 패턴 (Day 1~7)

- **Day 1: LCEL chain + Pydantic 구조화 출력**: `ChatPromptTemplate | ChatBedrockConverse.with_structured_output(schema)`. 업무 매칭·기준일 규칙·규칙 정규화·JOB 설명→규칙·채팅 조건·채팅 답변을 Pydantic 모델 6종으로 받고, 형식이 다르면 실패로 처리 (`src/agent.py`)
- **Day 2: RAG (하이브리드 검색)**: 업무 설명을 `##` 절 단위로 나눠 BM25(kiwipiepy 형태소 + 글자 2-gram), Chroma 벡터(Bedrock Titan 임베딩), 제목 일치를 RRF로 결합. 임베딩을 못 쓰면 BM25만으로 동작 (`src/retriever.py`)
- **Day 3~4: 도구 다중**: `get_registration_schedule`, `find_jobs_by_date`, `retrieve_business_docs`, `verify_registration_file`. 채팅에서는 AI가 뽑은 조건에 따라 코드가 도구를 고릅니다. 자유 ReAct 대신 이 방식을 택한 이유는 회고 참고 (`src/tools.py`, `src/chat.py`)
- **Day 5: 가드레일**
  - 입력: 요청 횟수, 500자, 개인정보, 데이터 변경·작업 요청을 AI 호출 전에 코드로 차단
  - 출력: 답변의 인용 목록이 정답 목록과 정확히 같은지, 문장 속 날짜·JOB ID가 계산 결과에 있는지 대조
  - 파일: xlsx 매크로·수식·외부 연결 차단
- **Day 5: HITL**: LangGraph `interrupt()`로 AI 제안(근거 원문 + 3개월 미리보기)을 사용자가 확인·수정·거부. 근거 문장이 원문에 없으면 확인 불가 (`src/confirm.py`)
- **Day 5: 미들웨어**: 시도당 30초·재시도 1회, AI 입력·로그·트레이스의 자격 증명·개인정보 마스킹, 채팅 이력 요약 (`src/agent.py`, `src/chat.py`)
- **Day 7: Observability**: `LANGSMITH_TRACING=true`면 채팅 그래프·도구 호출·LLM 호출(시도 횟수·결과)을 마스킹해 LangSmith에 기록
- **Day 7: 평가**: `evaluation/test_queries.csv` 20건 자체 평가(코드 검사 + 도구 검사 + AI 판정)와 RAGAS 4개 지표 (`evaluation/run_eval.py`)

## 아키텍처

```
[Streamlit src/app.py] 등록 일정 매트릭스 │ 기준일·규칙 확인 │ xlsx 검증 │ 채팅
        │                 (세 화면 모두 views.load_state의 같은 계산 결과 사용)
        ├─ src/confirm.py  확인 그래프 (LangGraph)
        │     plan(확인할 항목) → propose(대기 제안 재사용 / AI 제안 + 근거 대조 + 미리보기)
        │     → review: interrupt() 확인·수정·거부 → confirmed.json 저장 → 다시 plan
        ├─ src/chat.py     채팅 그래프 (LangGraph)
        │     guard(코드 가드레일) → parse(AI: 질문→조건) → schedule | business(도구 호출)
        │     → compose(AI 답변) → 정답 목록·근거와 대조 (어긋나면 버림)
        ├─ src/tools.py    도메인 도구 (일정 조회·날짜별 JOB·기준일 근거·xlsx 검증)
        ├─ src/retriever.py 업무 설명 하이브리드 검색 (BM25 + Chroma + 제목 일치, RRF)
        ├─ src/agent.py    단일 LLM 함수 LlmClient (Bedrock Claude, 30초·재시도, 스키마 검증, 마스킹, 트레이스), ask()
        ├─ src/engine.py   계산 엔진 (영업일, 기준일 규칙, D±N, 등록일 16:30 기준)
        ├─ src/store.py    confirmed.json / pending.json (근거 해시로 무효화, 원자적 저장)
        └─ src/verify.py   xlsx 파일 가드레일 + 정상·누락·초과 판정
data/ (MD 3개 · confirmed.json · pending.json)
```

## 실행 방법

```bash
# 가상 환경 (Python 3.12, mini-pjt/.venv)
uv venv .venv --python 3.12
uv pip install -r requirements.txt --python .venv/Scripts/python.exe
cp .env.example .env        # AWS 리전·자격 증명, BEDROCK_MODEL_ID (예: us.anthropic.claude-sonnet-4-6)

.venv\Scripts\streamlit run src/app.py
```

- 처음 실행하면 "기준일·규칙 확인" 화면에서 AI 제안을 확인(또는 직접 입력)해 확정합니다. 확정은 한 번이면 모든 달에 쓰입니다.
- AI 설정이 없어도 직접 입력으로 확정하면 일정·검증은 같은 결과로 동작합니다(채팅만 꺼짐).
- 테스트: `.venv\Scripts\python -m pytest -q` (402개, 네트워크 없이 가짜 LLM 사용)
- 단계별 검증: `.venv\Scripts\python tasks/verify.py NN`
- 자체 평가(실제 Bedrock): `.venv\Scripts\python evaluation/run_eval.py --round 2`
- 채팅 함수: `from src.agent import ask; ask("B001은 12월에 언제 등록돼?", "2026-11")` → `{"answer", "contexts", "trace"}` (`api_spec.md` 형식)

## RAGAS 평가 결과
2차 평가 기준. 근거(contexts)가 있는 답변 10건을 Bedrock Claude Sonnet 4.6과 Titan 임베딩으로 측정했습니다.

| 지표 | 1차 | 2차 |
|---|---|---|
| context_recall | 0.833 | **0.942** |
| context_precision | 0.633 | **0.517** |
| faithfulness | 0.933 | **0.947** |
| answer_relevancy | 0.683 | **0.679** |

- 2차에서 근거(basis) 문장을 contexts에 더해 recall은 올랐습니다. 반면 날짜 조회의 contexts에 JOB마다 근거 문장이 길게 붙으면서 precision은 내려갔습니다.
- answer_relevancy는 답변에 등록 이유 설명이 붙으면서 질문과의 직접 대응도가 낮게 잡혔습니다 (회고의 남은 한계 참고).

## 인-아웃 세트 통과율 (자체 평가)
- 1차: 13 / 20 통과 (guardrail 3/3) → [evaluation/round1_report.md](evaluation/round1_report.md)
- 2차: **19 / 20** 통과 (positive 7/8, negative 5/5, edge 4/4, guardrail 3/3) → [evaluation/round2_report.md](evaluation/round2_report.md)
- 개선폭: +6건. 주요 개선 사항:
  - JOB 찾기를 도구로 옮겨 없는 JOB도 `get_registration_schedule` 결과로 답함
  - 기준일 근거(출발 날짜·요일 → 기준일 D)를 도구 결과에 넣어 "12-05가 토요일이라 12-07로 이동" 같은 설명을 가능하게 함
  - 업무 답변에 출처 표시
  - 평가 지침 명확화: 근거 설명에서 언급한 날짜는 금지 위반이 아님
- 성공 기준(18/20 이상, guardrail 3/3, faithfulness ≥ 0.8) 충족

## 트라이앤에러 회고
- **시도했지만 실패한 접근 · 왜 실패했는가**
  - **얇은 프롬프트로 확인 제안 받기**: 실제 Sonnet 4.6의 제안이 기대값과 2/16만 일치했습니다.
    - 근거를 원문 복사가 아니라 해설로 씀
    - 정규화 중 원문에 없는 규칙을 추가함
    - 기준일 묶음 이름 대신 개별 이름을 `·`로 나열함
    - 근거 대조 가드레일이 대부분 막았지만 쓸 수 없는 수준이었습니다.
  - **"마감일" 검색을 BM25만으로**: 결산 절 본문에 "마감일"이 있어 결산이 1순위로 나왔습니다.
  - **업무 질문에 그 달 기준일 목록을 항상 넘기기**: AI가 "입금반영은 언제?" 질문에 날짜를 직접 계산해 나열했고, 출력 검증에서 버려졌습니다.
  - **첫 엔진 설계**: 기준일 규칙이 참조하는 다른 기준일(예: 마감일 → 자동이체 응당일)의 확정 여부를 보지 않았습니다. 일부만 확정되면 조용히 틀린 날짜가 나올 수 있었습니다.
- **최종 채택한 접근 · 왜 그것으로 갔는가**
  - **날짜는 엔진만 계산하고 AI는 "규칙 문장 제안"과 "문장화"만**: 결과가 결정적이고 테스트 가능합니다. 같은 입력이면 매트릭스·검증·채팅의 날짜가 같습니다.
  - **확인 제안 프롬프트**: 원문 그대로 인용, 규칙 추가 금지, 샘플과 다른 가상 업무의 예시(카드결제·정산)를 넣었습니다. 여기에 참조 기준일 존재 검사와 문장별 근거 대조를 더해 14/16 일치, 전부 확인 가능이 됐습니다. 남은 2건은 의미가 같은 분할 표현입니다.
  - **채팅에서 자유 ReAct 대신 "AI가 조건 추출 → 코드가 도구 선택"**: 정답 목록을 코드가 만들어야 인용을 정확히 대조할 수 있습니다. 도구 호출은 트레이스에 그대로 남습니다.
  - **하이브리드 검색에 "제목 일치" 신호 추가**: 업무 표현이 절 제목을 가리키는 도메인 특성을 반영했습니다.
- **남은 한계 · 향후 개선 방향**
  - #4(결산 배치 A002)는 답변이 "출발 11-02 → 기준일 11-03"을 정확히 설명했는데, 평가 AI가 11-02 언급을 금지 항목("마감일 11-02 안내") 위반으로 판정했습니다. 평가 AI의 판정 흔들림을 줄이려면 금지 항목을 "11-02를 마감일로 안내"처럼 더 구체적으로 쓰는 것이 좋습니다.
  - context_precision(0.517)과 answer_relevancy(0.679)가 낮습니다. 날짜 조회 contexts를 질문에 필요한 근거만 남기도록 줄이고, 설명을 질문 유형에 따라 선택적으로 붙이는 개선이 필요합니다.
  - `매 <업무> <구분>` 묶음은 JOB 규칙에 적힌 기준일 이름에서 고릅니다. 어떤 JOB에도 직접 적히지 않은 기준일은 직접 입력이 필요합니다.
  - 데이터 변경·작업 요청 차단은 키워드 정규식과 AI 분류의 2중 방어입니다. 우회 표현이 나오면 키워드를 보강해야 합니다.

## 핵심 코드 위치
- `src/confirm.py:355`: 확인 그래프 (LangGraph `interrupt()` HITL), `plan_items` :111, `propose` :221, `decide` :304
- `src/chat.py:113`: 채팅 그래프 `ChatService` (가드레일 :171, 일정 조회 :232, 답변·검증 :315, 정답 대조 :375)
- `src/agent.py:347`: 단일 LLM 함수 `LlmClient` (재시도·시간 제한·스키마 검증), 프롬프트 :218, 마스킹 :86, `ask()` :455
- `src/tools.py:26`: 도메인 도구 `ScheduleTools`, 근거 설명 `Explainer` :133
- `src/retriever.py:83`: RAG 하이브리드 검색 `BusinessRetriever`, 근거 문장 대조 :66
- `src/engine.py:501`: 계산 엔진 `compute_schedule`, 기준일 `BaseDates` :247, 등록일 규칙 :78
- `src/verify.py:66`: xlsx 파일 가드레일, 판정 :214
- `src/store.py:118`: 확정값 저장소 (근거 해시 무효화)
- `src/app.py`: Streamlit 화면 (일정 :104, 확인 :138, 검증 :201, 채팅 :234)
- `evaluation/run_eval.py`: 자체 평가·RAGAS
