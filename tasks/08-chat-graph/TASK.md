# 08단계 · 채팅 그래프

**목표**: 문장 질의에 계산 결과만 근거로 답한다.

**선행 단계**: [02-calculation-engine](../02-calculation-engine/TASK.md), [05-ai-foundation](../05-ai-foundation/TASK.md), [06-rag](../06-rag/TASK.md)

## 체크리스트

- [x] 입력 가드레일(AI 호출 전): 개인정보 마스킹·거부(`PII_MASKED`), 500자까지 처리·501자부터 `INPUT_TOO_LONG`, 60초 안 10번째까지 처리·11번째부터 `RATE_LIMITED`(가짜 시계로 테스트), 데이터 변경 요청 거부
- [x] 범위 밖 분류 → 고정 문장 "배치 작업 등록 일정 범위 밖의 내용이라 알 수 없어요."
- [x] 질문 → 조건 `{JOB, 월, 일}` (연도 없는 월은 고른 달 포함 12개월 안)
- [x] 조회 범위 제한: 고른 달 기준 앞 1개월 ~ 뒤 11개월 (`OUT_OF_RANGE_MONTH`)
- [x] ReAct 도구 호출: `get_registration_schedule`, `find_jobs_by_date`, `retrieve_business_docs`
- [x] 코드가 정답 목록 생성 → 답변 인용 목록과 정확히 비교 → 문장 포함 확인
- [x] 미확정 JOB 제외, 질문 달의 차단 오류는 오류 코드로 표시
- [x] 채팅 이력 요약 (미들웨어)
- [x] 조회 범위 안이라도 필요한 달력(M-1·M·M+1)이 없으면 `CALENDAR_MISSING`과 없는 달 표시 (현재 data/에서 "1월" → 2027-02 없음)
- [x] 정답 예시: 화면 2026-11에서 "B001은 12월에 언제 등록돼?" → 2026-12-06, "25일에 등록되는 배치는 뭐야?" → B106·B201·D001·E001

## 만들 파일

- `src/chat.py` (채팅 그래프: 가드레일·조건 추출·도구 호출·답변 검증, `ChatService`)
- `src/tools.py` (도메인 도구: `get_registration_schedule`, `find_jobs_by_date`, `verify_registration_file`, 기준일 사실)
- `src/agent.py`의 `ask(question, year_month)` (api_spec.md 형식, `src/chat.py`로 위임)
- `tests/test_chat.py`
- `tests/test_out_of_scope.py`
- `tests/test_chat_guardrails.py`

## 참고 사항

- 도구 선택은 AI가 뽑은 조건(kind·JOB·날짜)에 따라 코드가 합니다. 답의 근거를 계산 결과로 한정하기 위해서입니다.
- 업무 질문은 특정 달을 물을 때만 그 달 기준일을 사실로 넘기고, 규칙을 묻는 질문은 업무 설명 원문으로만 답합니다.
- 일정 답변의 문장 속 날짜는 계산 결과의 등록일·수행일만 허용합니다.
- 실제 Bedrock(Sonnet 4.6)으로 `evaluation/test_queries.csv` 20건을 돌려 모두 기대 동작과 일치했습니다 (10단계에서 RAGAS로 정식 평가).

## 관련 인수 조건 (Seed)

- `ac_chat_grounded`
- `ac_out_of_scope`
- `ac_chat_guardrails`

## 완료 기준

`expected-202611.md`의 채팅 예시 3건이 정답 목록과 일치하고, 범위 밖·가드레일 테스트가 통과한다.

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 08          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 08 --only   # 이 단계 테스트만
   ```

2. **샘플 대조**: `samples/expected-202611.md` 4절의 채팅 예시 3건과 대조

3. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
