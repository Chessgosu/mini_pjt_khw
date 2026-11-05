# 04단계 · xlsx 검증

**목표**: 등록 내역 파일의 유효성을 검사하고 누락·초과를 판정한다. 파일 내용은 데이터로만 다룬다.

**선행 단계**: [02-calculation-engine](../02-calculation-engine/TASK.md)

## 체크리스트

- [x] 파일 이름 `BATCH_SCHEDULE_YYYYMM.xlsx` 검사, 존재할 수 없는 연월 검사
- [x] 실제 xlsx(zip) 여부 검사 (확장자만 바꾼 파일 차단)
- [x] `UNSAFE_CONTENT`: 매크로(`vbaProject.bin`), 외부 데이터 연결, OLE·포함 개체, 수식
- [x] 레이아웃: 첫 시트, 1행 헤더 `JOB ID`·1~말일, 셀은 대문자 `O` 또는 빈칸
- [x] `EMPTY_JOB_ID`, `DUPLICATE_JOB_ID`, `FILE_TOO_LARGE`(10MB, 1,000행)
- [x] 형식 오류는 "잘못된 파일입니다" + 사유 코드 전부 반환, 판정하지 않음
- [x] 판정: 정상 / 누락 / 초과, 규칙에만 있는 JOB은 전부 누락, 파일에만 있는 JOB은 제외·경고
- [x] 검증 연월은 파일 이름 기준
- [x] 셀 내용을 AI에 보내지 않고, 업로드로 `data/`가 바뀌지 않음

## 만들 파일

- `src/verify.py` (xlsx 유효성 검사·판정; 08단계에서 `tools.py`의 `verify_registration_file` 도구가 이 모듈을 감쌈)
- `tests/test_file_safety.py`
- `tests/test_verify.py`
- `tests/test_guardrails.py`

## 관련 인수 조건 (Seed)

- `ac_file_safety`
- `ac_verdicts`
- `ac_guardrail_codes`

## 완료 기준

`samples/correct` → 모두 정상, `samples/with-errors` → 누락 2·초과 1·경고 2, `samples/invalid` 10종 → 모두 "잘못된 파일입니다"와 기대 사유 코드.

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 04          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 04 --only   # 이 단계 테스트만
   ```

2. **샘플 대조**: `samples/correct`(모두 정상), `samples/with-errors`(누락 2·초과 1·경고 2), `samples/invalid/expected.md`(10종)와 대조

3. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
