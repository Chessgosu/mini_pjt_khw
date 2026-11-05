# 00단계 · 환경 준비

**목표**: 개발 기반을 만들고 테스트를 실행할 수 있게 한다.

**선행 단계**: 없음

## 체크리스트

- [ ] `src/`, `tests/`, `evaluation/` 폴더 구성 (develop_standard.md)
- [ ] `.env.example` 작성 (AWS 리전, 자격 증명, Bedrock 모델 ID, `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`)
- [ ] `pytest.ini`에 테스트 경로 설정
- [ ] `tests/conftest.py`: 임시 data 폴더 fixture (실제 `data/confirmed.json`을 건드리지 않음)
- [ ] `tests/conftest.py`: 가짜 LLM fixture (네트워크 없이 정해진 응답 반환)
- [ ] `pip install -r requirements.txt` 설치 확인
- [ ] `tests/conftest.py`: 가짜 시계 fixture (채팅 요청 횟수 제한 60초 창 테스트용)
- [ ] `tests/fixtures/` 준비: 테스트 전용 달력(2025-10 연휴 10-03~10-09, 2026-09), 가상 업무 "정산", 규칙 칸이 빈 가상 JOB X001

## 만들 파일

- `.env.example`
- `tests/conftest.py`
- `pytest.ini`

## 완료 기준

`python -m pytest -q`가 오류 없이 실행된다 (테스트 0건이어도 됨).

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 00          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 00 --only   # 이 단계 테스트만
   ```

2. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
