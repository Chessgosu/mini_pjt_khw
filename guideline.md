5. 빌드 체크리스트 · 12개 패턴
Day 1~7 에서 배운 패턴 중 6개 이상 적용 권장.

#	패턴	Day	적용 예
1	LCEL chain (Pydantic 구조화 출력)	Day 1	답변을 dict 로 반환
2	ReAct (도구 자율 선택)	Day 3	bind_tools 로 도메인 도구 결합
3	RAG (하이브리드 검색·리랭킹·쿼리 확장)	Day 2	retrieve_docs 로 정책·리서치 검색
4	도구 다중 (DB·계산기·외부 API)	Day 4	한 질의에 도구 2~3개 자율 결합
5	MCP 서버 연동	Day 4	사내 시스템을 MCP 서버로 노출·호출
6	가드레일 (PII·프롬프트 인젝션 방어)	Day 5	입력·출력 필터링
7	HITL (위험 작업 승인)	Day 5	interrupt() 로 사용자 승인
8	미들웨어 (요약·마스킹·재시도)	Day 5	대화 이력 요약, PII 마스킹
9	Multi-Agent Supervisor	Day 6	서브 에이전트 역할 분할·위임
10	Plan-Execute · 장기 메모리	Day 7	복잡한 작업 단계 분해 · LangGraph Store
11	Observability · Trace	Day 7	LangSmith / LangFuse 트레이스 기록
12	평가 (RAGAS · LLM-as-Judge)	Day 7	자체 평가 세트로 지표 산출
필수 (산출물 규약 상 요구): 1, 3, 11, 12 (Docker·API·트레이스·평가)
권장 (배점 유리): 위 중 6개 이상 자유 조합