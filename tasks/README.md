# 개발 TASK

Seed v1.20.0의 인수 조건 17개를 10단계(00~10)로 나눈 개발 계획입니다. 단계 폴더의 `TASK.md` 체크리스트로 진행 상황을 관리합니다.

## 진행 순서

```
00 → 01 → 02 ─┬─→ 03 ───────────────────┐
               ├─→ 04 ───────────────────┤
               └─→ 05 → 06 → 07 → 08 ───┴─→ 09 → 10
```

- **02 계산 엔진**이 가장 중요합니다. 샘플 정답(`O` 85개)과 100% 일치해야 다음 단계로 갑니다.
- 04까지 끝나면 AI 없이 일정 보기·검증이 동작합니다 (중간 점검 시점).
- 05는 00 이후 바로 시작할 수 있어 02와 병행 가능합니다.

## 검증

`mini-pjt` 폴더에서 단계 번호를 주고 실행합니다. 이 단계와 선행 단계의 테스트를 함께 돌려 이전 단계가 깨지지 않았는지도 확인합니다.

```bash
python tasks/verify.py 02          # 02단계 + 선행 단계
python tasks/verify.py 02 --only   # 02단계만
python tasks/verify.py all         # 전체
```

테스트 통과 후 각 TASK.md의 "샘플 대조"까지 맞으면 완료(✅)로 표시합니다.

## 단계 목록

| 단계 | 폴더 | 완료 기준 (AC) | 상태 |
|---|---|---|---|
| 00 · 환경 준비 | [00-environment](00-environment/TASK.md) | - | ✅ |
| 01 · 입력 파싱 | [01-input-parsing](01-input-parsing/TASK.md) | - | ✅ |
| 02 · 계산 엔진 ⭐ | [02-calculation-engine](02-calculation-engine/TASK.md) | `ac_engine_correct`, `ac_base_date_rules`, `ac_sample_202611` | ✅ |
| 03 · 확정 저장소 | [03-confirmed-store](03-confirmed-store/TASK.md) | `ac_invalidation` | ✅ |
| 04 · xlsx 검증 | [04-xlsx-verification](04-xlsx-verification/TASK.md) | `ac_file_safety`, `ac_verdicts`, `ac_guardrail_codes` | ✅ |
| 05 · AI 연동 기반 | [05-ai-foundation](05-ai-foundation/TASK.md) | `ac_ai_fallback`, `ac_observability` | ✅ |
| 06 · RAG | [06-rag](06-rag/TASK.md) | - | ✅ |
| 07 · 확인 그래프 (HITL) | [07-confirmation-graph](07-confirmation-graph/TASK.md) | `ac_ai_confirm_gate`, `ac_untrusted_input` | ✅ |
| 08 · 채팅 그래프 | [08-chat-graph](08-chat-graph/TASK.md) | `ac_chat_grounded`, `ac_out_of_scope`, `ac_chat_guardrails` | ✅ |
| 09 · Streamlit 화면 | [09-streamlit-ui](09-streamlit-ui/TASK.md) | `ac_matrix_display`, `ac_same_dates_everywhere` | ⬜ |
| 10 · 평가·문서 | [10-evaluation](10-evaluation/TASK.md) | `ac_evaluation` | ⬜ |

상태 표시: ⬜ 시작 전 · 🟨 진행 중 · ✅ 완료
