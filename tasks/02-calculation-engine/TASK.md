# 02단계 · 계산 엔진 ⭐

**목표**: 규칙과 확정값으로 수행일과 등록일을 결정적으로 계산한다. 서비스의 핵심이다.

**선행 단계**: [01-input-parsing](../01-input-parsing/TASK.md)

## 체크리스트

- [x] 영업일 판정: 주말 + `business-calendar.md` 휴일 제외
- [x] 월 기준 규칙: 매월 N일(없으면 말일), 말일, 첫·마지막 영업일, N째 주 X요일(없으면 수행 안 함)
- [x] 매일 수행 규칙: `매일`, `매 영업일`
- [x] 기준일 표현: `<업무> <구분>`, 여러 기준일 `A,B,C<구분>`, `매 <업무> <구분>`
- [x] D 연산: `D일`, `D±N일`, `D영업일`, `D±N 영업일`, `D일 ± N영업일`, `영업일 D일 ± N일`
- [x] 표준 문법 판정: 허용 / 정규화 대상 / 거부(`MD_SYNTAX_ERROR`)
- [x] 등록일 규칙: 16:30~23:59 → 당일, 00:00~16:29 → 전날(달력 기준)
- [x] 앞 달·대상 월·다음 달 3개월 계산 후 등록일이 대상 월인 것만 반환
- [x] `CALENDAR_MISSING`: 대상 월 M에는 최소 M-1·M·M+1월 달력 필요, 계산 중 들른 다른 달도 필요. 없는 달 표시 (현재 data/는 2026-11·12만 조회 가능)
- [x] 기준일 규칙: ① 출발 날짜(월 기준 규칙 또는 `<다른 기준일>과 같음`) ② 휴일 보정 ③ 겹침 보정 ④ 겹침 제외(자동이체 응당일: 원래 출금일이 가장 빠른 것만 남김). 순환 참조는 `MD_SYNTAX_ERROR`. 한 번 확정한 규칙으로 매달 계산
- [x] 고정 테스트 모음 `tests/fixtures/` 20개 이상 (주말·공휴일·말일·월 경계·연말 경계·없는 N째 요일·달력 범위 밖)
- [x] 규칙 우선순위: 수행일은 수행일 규칙으로만 계산 (JOB 이름·설명 무시, 예: A001은 첫 영업일)
- [x] fixture 업무 "정산": D가 휴일(2026-09-20 일)인 경우와 영업일(2026-09-21 월)인 경우 모두 D 연산 검증

## 만들 파일

- `src/engine.py` (계산 엔진: 문법 판정, 기준일 규칙, D 연산, 등록일, 3개월 계산)
- `tests/helpers.py` (샘플 확정값, fixture 읽기)
- `tests/fixtures/`
- `tests/test_engine.py` (문법·등록일 규칙, fixture 달력)
- `tests/test_base_date_rules.py` (기준일 규칙·겹침 제외, 실제 data/ + 2025-10 fixture)
- `tests/test_sample_202611.py` (2026-11 샘플 `O` 85개 대조)

## 관련 인수 조건 (Seed)

- `ac_engine_correct`
- `ac_base_date_rules`
- `ac_sample_202611`

## 완료 기준

`samples/expected-202611.md`의 기대값과 **100% 일치** (`O` 85개). Seed의 예시(2026-09-20, 2026-12 응당일, 10월 말일자 → 11-01, 마감일 11-03)가 모두 맞는다.

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 02          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 02 --only   # 이 단계 테스트만
   ```

2. **샘플 대조**: `samples/expected-202611.md`의 JOB별 등록일, 기준일 표와 대조 (`O` 85개)

3. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
