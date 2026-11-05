# 미니 PJT: [프로젝트 이름]

## 무엇을 푸나
한 문장으로 사용자 문제.

## 활용한 패턴 (Day 1~7)
- Day X: 패턴 — 어떻게 활용
- Day Y: 패턴 — ...

## 아키텍처
(간단 다이어그램 or 텍스트 설명)

## 실행 방법
{실행 방법}

## RAGAS 평가 결과
- context_recall: 0.XX
- context_precision: 0.XX
- faithfulness: 0.XX
- answer_relevancy: 0.XX

## 인-아웃 세트 통과율 (자체 평가)
- 1차 (Day 9 종료): XX / XX 통과
- 2차 (Day 10 개선 후): XX / XX 통과
- 개선폭: +XX 건 (주요 개선 사항: ...)

## 트라이앤에러 회고
- 시도했지만 실패한 접근 · 왜 실패했는가
- 최종 채택한 접근 · 왜 그것으로 갔는가
- 남은 한계 · 향후 개선 방향

## 핵심 코드 위치
- `src/agent.py:NN` — 메인 에이전트 그래프
- `src/tools.py` — 도메인 도구
- `src/retriever.py` — RAG 파이프라인