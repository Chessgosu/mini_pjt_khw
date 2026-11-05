mini-pjt/
├── src/                       # 소스코드
│   ├── agent.py               # 메인 에이전트 그래프
│   ├── tools.py               # 도메인 도구
│   ├── retriever.py           # RAG 파이프라인
│   └── ...
├── data/                      # 사용한 문서·데이터
├── evaluation/
│   ├── test_queries.csv       # 인-아웃 세트 (§4-4 스키마 필수 · 자체 평가·최종 채점의 기준)
│   ├── round1_report.md       # 1차 자체 평가 결과 (Day 9)
│   └── round2_report.md       # 2차 자체 평가 결과 (Day 10 · 개선 후)
├── SERVICE.md                 # 서비스 스펙 (§4-3 템플릿 필수)
├── requirements.txt
├── README.md                  # §4-5 템플릿 준수
└── run.sh                     # 실행 스크립트 (선택)