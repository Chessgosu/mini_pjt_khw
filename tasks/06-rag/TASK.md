# 06단계 · RAG

**목표**: 업무 설명 문서에서 관련 절을 찾아 AI 판단의 근거로 쓴다.

**선행 단계**: [01-input-parsing](../01-input-parsing/TASK.md), [05-ai-foundation](../05-ai-foundation/TASK.md)

## 체크리스트

- [ ] `business-explanation.md`를 `##` 절 단위로 분할
- [ ] BM25 검색 (kiwipiepy 한국어 형태소 분석)
- [ ] 벡터 검색 (Chroma, Bedrock 임베딩)
- [ ] 하이브리드 결합 (점수 결합 또는 순위 융합)
- [ ] 근거 문장 검사: 공백 정규화 후 원문 포함 여부, 없으면 "근거 불일치"
- [ ] `retrieve_business_docs` 도구로 노출

## 만들 파일

- `src/retriever.py`
- `tests/test_retriever.py`

## 관련 인수 조건 (Seed)

- (07·08단계와 RAGAS 평가에서 사용)

## 완료 기준

"마감일", "자동이체 응당일" 질의에 해당 업무 절이 1순위로 나온다.

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 06          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 06 --only   # 이 단계 테스트만
   ```

2. **샘플 대조**: "마감일", "자동이체 응당일" 질의 결과의 1순위 절 확인

3. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
