# 10단계 · 평가·문서

**목표**: 자체 평가로 성공 기준을 확인하고 산출물을 완성한다.

**선행 단계**: [09-streamlit-ui](../09-streamlit-ui/TASK.md)

## 체크리스트

- [ ] `evaluation/test_queries.csv` 20건 실행 스크립트 (expected_traits·forbidden·expected_tools 판정)
- [ ] RAGAS 측정: context_recall, context_precision, faithfulness, answer_relevancy
- [ ] 1차 평가 → `evaluation/round1_report.md`
- [ ] 실패 케이스 분석·개선
- [ ] 2차 평가 → `evaluation/round2_report.md`
- [ ] `README.md`의 RAGAS 결과, 통과율, 회고, 핵심 코드 위치 기입
- [ ] 자체 평가는 실제 Bedrock으로 **수동 실행**. 오프라인 테스트는 판정 로직(expected_traits·forbidden·expected_tools)만 가짜 LLM 응답으로 확인

## 만들 파일

- `evaluation/run_eval.py`
- `evaluation/round1_report.md`
- `evaluation/round2_report.md`
- `README.md`

## 관련 인수 조건 (Seed)

- `ac_evaluation`

## 완료 기준

20건 중 18건 이상 통과, guardrail 3건 전부 통과, RAGAS faithfulness 0.8 이상.

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 10          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 10 --only   # 이 단계 테스트만
   ```

2. **샘플 대조**: `evaluation/round2_report.md`의 통과율·RAGAS 점수가 성공 기준(18/20, guardrail 3/3, faithfulness 0.8) 이상인지 확인

3. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
