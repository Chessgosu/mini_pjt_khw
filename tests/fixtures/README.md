# 테스트 전용 데이터 (fixture)

실제 `data/`에 없는 달·업무·JOB이 필요한 사례만 여기서 검증합니다. 운영 데이터로 쓰지 않습니다.

| 파일 | 용도 | 관련 인수 조건 |
|---|---|---|
| `calendar-2025.md` | 2025-09 ~ 2025-11 실제 공휴일. 2025-10 추석 연휴(10-03 ~ 10-09)로 5일자·10일자 응당일이 10-10으로 겹치는 사례 | `ac_base_date_rules` (겹침 제외) |
| `calendar-2026-09-virtual.md` | **가상** 달력 2026-08 ~ 2026-10, 2026-09에 공휴일 없음 (실제 2026-09에는 추석이 있음). D 연산 검증 전용 | `ac_engine_correct` |
| `business-explanation-settlement.md` | 가상 업무 "정산" (기준일 규칙 "매월 20일", 휴일 보정 없음) | `ac_engine_correct` |
| `batch-schedule-x001.md` | 수행일 규칙 칸이 빈 가상 JOB X001 (JOB 설명 기반 규칙 제안) | `ac_ai_confirm_gate` |
