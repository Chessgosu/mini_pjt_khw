# 05단계 · AI 연동 기반

**목표**: 모든 AI 호출이 거치는 공통 기반을 만든다.

**선행 단계**: [00-environment](../00-environment/TASK.md)

## 체크리스트

- [ ] 단일 LLM 함수: Claude on AWS Bedrock (`langchain-aws`), 모델 ID·리전은 `.env`
- [ ] 미들웨어: 시도당 30초, 재시도 1회, 실패·초과·스키마 불일치 시 직접 입력 경로로 전환
- [ ] Pydantic 출력 스키마: 업무 매칭, **기준일 규칙**, 규칙 정규화, JOB 설명→규칙 목록, 채팅 조건, 채팅 답변+인용 목록
- [ ] 미들웨어: 로그·트레이스의 자격 증명·개인정보 마스킹
- [ ] LangSmith 트레이스: `LANGSMITH_TRACING` 켜면 기록, 꺼도 정상 동작
- [ ] AI가 없을 때 직접 입력으로 같은 결과가 나오는 경로

## 만들 파일

- `src/agent.py` (LLM 함수·스키마)
- `tests/test_ai_fallback.py`
- `tests/test_observability.py`

## 관련 인수 조건 (Seed)

- `ac_ai_fallback`
- `ac_observability`

## 완료 기준

가짜 LLM으로 실패·시간 초과·스키마 오류를 흉내 내도 직접 입력으로 같은 등록일이 나온다.

## 검증 방법

1. **단계 테스트** (`mini-pjt` 폴더에서 실행)

   ```bash
   python tasks/verify.py 05          # 이 단계 + 선행 단계 테스트
   python tasks/verify.py 05 --only   # 이 단계 테스트만
   ```

2. **인수 조건 확인** (선택): `ooo evaluate`로 위 "관련 인수 조건"이 충족되는지 확인

결과가 `✅ 통과`이고 샘플 대조가 맞으면 체크리스트를 모두 체크하고 [tasks/README.md](../README.md)의 상태를 ✅로 바꿉니다.

## 참고

- 상세 명세: [docs/service-spec.md](../../docs/service-spec.md)
- 개발 명세: [batch-schedule-checker.v2.seed.yaml](../../batch-schedule-checker.v2.seed.yaml)
- 개발 지침: [CLAUDE.md](../../CLAUDE.md)
