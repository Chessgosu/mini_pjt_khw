# 09단계 · Streamlit 화면

**목표**: 4개 화면으로 서비스를 사용할 수 있게 한다.

**선행 단계**: [03-confirmed-store](../03-confirmed-store/TASK.md), [04-xlsx-verification](../04-xlsx-verification/TASK.md), [07-confirmation-graph](../07-confirmation-graph/TASK.md), [08-chat-graph](../08-chat-graph/TASK.md)

## 체크리스트

- [ ] 등록 일정: 연월 선택 → JOB × 날짜 매트릭스, 미확정 JOB 행 표시
- [ ] 기준일·규칙 확인: 업무 매칭·기준일 규칙·정규화 규칙의 AI 제안·근거·3개월 날짜 미리보기 표시, 확인·수정·거부·직접 입력 (규칙은 한 번만 확정)
- [ ] 검증: xlsx 업로드 → 잘못된 파일 사유 또는 판정 매트릭스(초록·빨강·주황, `verdict-*`)
- [ ] 채팅: 질문 입력·답변 표시, AI 미설정 시 안내
- [ ] 세 화면(매트릭스·검증·채팅)이 같은 엔진 결과를 쓰는지 확인
- [ ] 달력이 부족한 달(현재 data/ 기준 2026-10, 2027-01)을 고르면 `CALENDAR_MISSING`과 없는 달 표시

## 만들 파일

- `src/app.py`
- `tests/test_app_schedule.py`
- `tests/test_single_source.py`

## 관련 인수 조건 (Seed)

- `ac_matrix_display`
- `ac_same_dates_everywhere`

## 완료 기준

`streamlit run src/app.py`로 샘플 데이터의 11월 일정·검증·채팅이 모두 동작한다.

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 09          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 09 --only   # 이 단계 테스트만
   ```

2. **샘플 대조**: `streamlit run src/app.py`로 11월 매트릭스·검증·채팅을 직접 확인

3. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
