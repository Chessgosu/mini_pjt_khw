"""단계별 TASK 검증 스크립트.

사용법 (mini-pjt 폴더에서 실행):
    python tasks/verify.py 02          # 02단계 + 이전 단계(00~01) 테스트를 함께 실행
    python tasks/verify.py 02 --only   # 02단계 테스트만 실행
    python tasks/verify.py all         # 전체 테스트 실행

결과:
    종료 코드 0 = 통과, 1 = 실패 또는 테스트 파일 없음
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # mini-pjt

# 단계별 검증 대상. 선행 단계는 tasks/README.md의 진행 순서를 따른다.
STAGES: dict[str, dict] = {
    "00": {"name": "환경 준비", "tests": ["tests/test_environment.py"], "deps": []},
    "01": {"name": "입력 파싱", "tests": ["tests/test_parsing.py"], "deps": ["00"]},
    "02": {"name": "계산 엔진",
           "tests": ["tests/test_engine.py", "tests/test_base_date_rules.py", "tests/test_sample_202611.py"],
           "deps": ["01"]},
    "03": {"name": "확정 저장소", "tests": ["tests/test_invalidation.py"], "deps": ["02"]},
    "04": {"name": "xlsx 검증",
           "tests": ["tests/test_file_safety.py", "tests/test_verify.py", "tests/test_guardrails.py"],
           "deps": ["02"]},
    "05": {"name": "AI 연동 기반", "tests": ["tests/test_ai_fallback.py", "tests/test_observability.py"],
           "deps": ["00"]},
    "06": {"name": "RAG", "tests": ["tests/test_retriever.py"], "deps": ["01", "05"]},
    "07": {"name": "확인 그래프", "tests": ["tests/test_ai_confirm.py", "tests/test_injection.py"],
           "deps": ["03", "05", "06"]},
    "08": {"name": "채팅 그래프",
           "tests": ["tests/test_chat.py", "tests/test_out_of_scope.py", "tests/test_chat_guardrails.py"],
           "deps": ["02", "05", "06"]},
    "09": {"name": "Streamlit 화면", "tests": ["tests/test_app_schedule.py", "tests/test_single_source.py"],
           "deps": ["03", "04", "07", "08"]},
    "10": {"name": "평가·문서", "tests": ["tests/test_evaluation.py"], "deps": ["09"], "command": ["evaluation/run_eval.py"]},
}


def with_deps(stage: str) -> list[str]:
    """선행 단계를 모두 포함한 단계 목록 (중복 없이, 낮은 번호부터)."""
    seen: set[str] = set()

    def walk(s: str) -> None:
        if s in seen:
            return
        seen.add(s)
        for d in STAGES[s]["deps"]:
            walk(d)

    walk(stage)
    return sorted(seen)


def run(cmd: list[str]) -> int:
    print("  $", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def main(argv: list[str]) -> int:
    try:  # Windows 콘솔(cp949)에서도 한글·기호가 깨지지 않도록
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    target = argv[0].zfill(2) if argv[0] != "all" else "all"
    only = "--only" in argv

    if target == "all":
        print("[전체] python -m pytest -q")
        return run([sys.executable, "-m", "pytest", "-q"])
    if target not in STAGES:
        print(f"알 수 없는 단계: {argv[0]} (00~10 또는 all)")
        return 1

    stages = [target] if only else with_deps(target)
    print(f"[검증] {target}단계 · {STAGES[target]['name']}"
          + ("" if only else f"  (함께 확인: {', '.join(s for s in stages if s != target) or '없음'})"))

    tests: list[str] = []
    missing: list[str] = []
    for s in stages:
        for t in STAGES[s]["tests"]:
            (tests if (ROOT / t).exists() else missing).append(t)

    if missing:
        print("\n[미완료] 아직 없는 테스트 파일:")
        for m in missing:
            print("  -", m)

    code = 0
    if tests:
        print("\n[테스트]")
        code = run([sys.executable, "-m", "pytest", "-q", *tests])
    elif target == "00":
        print("\n[테스트] 00단계는 pytest 실행 가능 여부만 확인합니다.")
        code = run([sys.executable, "-m", "pytest", "-q", "--collect-only"])
        code = 0 if code in (0, 5) else code  # 5 = 수집된 테스트 없음

    command = STAGES[target].get("command")
    if command:
        script = ROOT / command[0]
        if script.exists():
            print("\n[평가] 실제 Bedrock 호출이 필요합니다.")
            code = code or run([sys.executable, *command])
        else:
            missing.append(command[0])
            print(f"\n[미완료] 아직 없는 평가 스크립트: {command[0]}")

    ok = code == 0 and not missing
    print("\n결과:", "✅ 통과" if ok else "❌ 미통과")
    print("※ 테스트 통과 후 samples/의 정답 문서와도 대조하고, 필요하면 `ooo evaluate`로 인수 조건을 확인하세요.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
