# 01단계 · 입력 파싱

**목표**: 입력 MD 3종을 읽어 구조화된 데이터로 바꾸고, MD 오류를 코드로 보고한다.

**선행 단계**: [00-environment](../00-environment/TASK.md)

## 체크리스트

- [x] `batch-schedule.md` 표 파서: JOB ID, JOB 이름, 수행일 규칙, 시작 시각, JOB 설명
- [x] `business-explanation.md`: `## 업무명` 절 단위 분리
- [x] `business-calendar.md`: `## YYYY-MM` 절과 휴일 목록, 절 존재 여부(입력 완료) 판단
- [x] 해시: 공백·줄바꿈 정규화 후 SHA-256 (업무 설명은 절 단위 + `##` 제목 목록, JOB은 행 단위)
- [x] `MD_FILE_MISSING`: 필요한 파일 없음·빈 파일·읽기 불가 (`business-explanation.md`는 필요할 때만)
- [x] `MD_SYNTAX_ERROR`: 필수 열 없음, JOB ID 중복
- [x] `START_TIME_INVALID`: 빈 칸, `24:00`, `9:05` 등
- [x] MD 파일은 계산할 때마다 다시 읽기
- [x] 규칙 문자열에서 업무 표현·구분·D 연산 분리 (예: `매월 마감일 D+2 영업일` → 업무 표현 "마감일", 연산 `D+2 영업일`)

## 만들 파일

- `src/parsing.py` (파싱 함수, 규칙 문자열 나누기)
- `src/models.py` (오류 코드 22개, 막는 범위, Finding)
- `tests/test_parsing.py`

## 관련 인수 조건 (Seed)

- ac_guardrail_codes (MD 관련 코드)

## 완료 기준

`data/`의 실제 MD 3개를 읽어 JOB 22개, 업무 4개, 달력 4개월이 나온다. MD 오류 코드별 테스트가 통과한다.

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 01          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 01 --only   # 이 단계 테스트만
   ```

2. **샘플 대조**: `data/`의 MD 3개를 읽은 결과가 JOB 22개, 업무 4개, 달력 4개월(2026-10 ~ 2027-01)인지 확인

3. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
