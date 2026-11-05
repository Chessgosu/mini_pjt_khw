# 07단계 · 확인 그래프 (HITL)

**목표**: AI 제안을 근거와 함께 보여 주고 사용자 확인 후에만 반영한다.

**선행 단계**: [03-confirmed-store](../03-confirmed-store/TASK.md), [05-ai-foundation](../05-ai-foundation/TASK.md), [06-rag](../06-rag/TASK.md)

## 체크리스트

- [x] 업무 매칭 제안: 표현과 제목이 같으면 바로 매칭, 다르면 AI 제안
- [x] 기준일 **규칙** 제안 (기준일마다 한 번, 예: `매월 5일, 휴일이면 다음 영업일, 앞선 자동이체 응당일과 겹치면 수행 안 함`), 근거 원문 문장 + 앞 달·대상 월·다음 달 날짜 미리보기
- [x] 규칙 정규화 제안: 표현만 다른 규칙 → 표준 표현
- [x] JOB 설명 → 규칙 목록 제안 (규칙 칸이 빈 JOB)
- [x] LangGraph `interrupt()`로 확인·수정·거부 대기
- [x] 확정 시 `confirmed.json` 저장 → 계산 엔진 재계산
- [x] 미확정 코드: `BUSINESS_MATCH_UNCONFIRMED`, `BASE_DATE_UNCONFIRMED`, `JOB_RULE_UNCONFIRMED`
- [x] MD 속 지시 문장은 데이터로만 전달 (프롬프트 인젝션 방어)
- [x] 한 번 확정한 규칙은 다른 달을 볼 때 다시 묻지 않음
- [x] 확인 도중 앱을 닫았다 열면 `pending.json`의 제안을 재사용하고 AI를 다시 호출하지 않음
- [x] fixture JOB X001(규칙 칸 비어 있음)로 JOB 설명 → 규칙 제안 검증

## 만들 파일

- `src/confirm.py` (확인 그래프: 항목 계획·제안·근거 검사·미리보기·결정, LangGraph `interrupt()`; `agent.py`와 `retriever.py`의 순환 import를 피하려고 별도 모듈)
- `tests/test_ai_confirm.py`
- `tests/test_injection.py`

## 참고 사항

- 엔진 보강: 기준일 규칙이 참조하는 기준일(같음·같으면·겹치면 묶음)까지 모두 확정되어야 그 기준일을 쓰는 JOB을 계산합니다 (미확정 참조로 조용히 틀린 날짜가 나오지 않도록).
- `매 <업무> <구분>` 묶음은 JOB 규칙에 직접 적힌 기준일 이름과 확정된 이름에서 고릅니다.
- 실제 Bedrock(Sonnet 4.6)으로 샘플을 돌린 결과: 16개 제안 중 14개가 기대값과 같고, 나머지 2개(B201·B202 쉼표 목록을 규칙 3개로 나눔)는 의미가 같아 확인 가능.

## 관련 인수 조건 (Seed)

- `ac_ai_confirm_gate`
- `ac_untrusted_input`

## 완료 기준

샘플 데이터에서 AI 제안(가짜 LLM)을 확정하면 `expected-202611.md` 1.3절의 기준일 규칙이 저장되고 11월 `O` 85개가 나오며, 확정 전에는 계산에 반영되지 않는다. fixture JOB X001은 규칙 ["매월 10일", "자동이체 20일자 응당일 D-1 영업일"]이 제안되고 11월 미리보기는 11-10, 11-19다.

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 07          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 07 --only   # 이 단계 테스트만
   ```

2. **샘플 대조**: 가짜 LLM으로 확정한 기준일 규칙이 `samples/expected-202611.md` 1.3절 규칙 표와 같은지, 계산된 날짜가 같은 절의 날짜 표와 같은지 확인

3. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
