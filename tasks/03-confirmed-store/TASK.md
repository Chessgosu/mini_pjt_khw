# 03단계 · 확정 저장소

**목표**: AI 제안을 사용자가 확정한 값을 안전하게 저장하고, 근거가 바뀌면 무효로 한다.

**선행 단계**: [02-calculation-engine](../02-calculation-engine/TASK.md)

## 체크리스트

- [x] `data/confirmed.json` 스키마: 업무 매칭, **기준일 규칙**, 수행일 규칙(정규화·JOB 설명 기반), 각 source_hash. 확정은 **규칙 단위**(달 단위 아님)
- [x] 원자적 저장: 임시 파일에 쓴 뒤 이름 변경, 남은 임시 파일은 시작 시 정리
- [x] 파싱 실패·스키마 불일치 → 전체 미확정
- [x] 무효화: 기준일 규칙(해당 업무 절), JOB 규칙(JOB 행 + 참조 업무 절), 업무 매칭(표현 + 제목 목록). **달력 수정은 확정 유지, 날짜만 재계산**
- [x] 공백·줄바꿈만 바뀐 경우, 참조하지 않는 업무 절만 바뀐 경우는 유지 (예: B001은 `## 급여` 절이 바뀌어도 유지)
- [x] `CONFIRM_SAVE_FAILED`: 잠김·쓰기 불가 시 미확정 유지
- [x] `data/pending.json`: 확정 전 AI 제안을 kind·target·source_hash 단위로 원자적 저장, 확정·거부·해시 변경 시 삭제, 깨지면 비어 있는 것으로 시작
- [x] 여러 항목 확정 시 항목별로 따로 저장, 일부 실패 시 그 항목만 `CONFIRM_SAVE_FAILED`
- [x] 동시 저장은 마지막 저장 반영(last-writer-wins)

## 만들 파일

- `src/store.py` (ConfirmedStore, PendingStore, 근거 해시, 원자적 쓰기)
- `tests/test_invalidation.py`

## 관련 인수 조건 (Seed)

- `ac_invalidation`

## 완료 기준

무효화·유지 시나리오와 저장 실패 테스트가 모두 통과한다.

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 03          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 03 --only   # 이 단계 테스트만
   ```

2. **샘플 대조**: 확정 → MD 수정 → 재계산 순서로 실제 `data/` 복사본에서 무효화가 일어나는지 확인

3. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
