"""확정값·대기 제안 저장소 (03단계).

- data/confirmed.json: 사용자가 확정한 업무 매칭, 기준일 규칙, 수행일 규칙 + 각 근거 해시
- data/pending.json: 확정 전 AI 제안 (계산에는 쓰지 않음)

확정은 규칙 단위이며 달 단위가 아니다. 근거(MD 원문)의 해시가 바뀌면 그 값만 미확정이 된다.
달력(business-calendar.md)은 근거가 아니므로 달력을 고쳐도 확정은 유지되고 날짜만 다시 계산된다.
저장은 항목마다 원자적으로 하고(임시 파일 → 이름 바꾸기), 동시 저장은 마지막 저장이 반영된다.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.engine import Confirmed, business_word, classify_rule
from src.models import Code, Finding
from src.parsing import Inputs, Job, text_hash

CONFIRMED_FILE = "confirmed.json"
PENDING_FILE = "pending.json"
SCHEMA_VERSION = 1
KINDS = ("business_matches", "base_date_rules", "run_rules")


# ================================================================ 원자적 쓰기


def atomic_write_json(path: Path, data: Any) -> None:
    """같은 폴더에 임시 파일로 쓴 뒤 os.replace로 바꾼다. 중간에 끊겨도 원본은 깨지지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def cleanup_temp_files(data_dir: Path) -> int:
    """저장 도중 끊겨 남은 임시 파일을 지운다 (앱 시작 시 호출)."""
    removed = 0
    for p in Path(data_dir).glob(".*.tmp"):
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _read_json(path: Path) -> tuple[dict | None, bool]:
    """(내용, 깨졌는지). 파일이 없으면 (None, False)."""
    if not path.exists():
        return None, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, True
    return (data, False) if isinstance(data, dict) else (None, True)


# ================================================================ 근거 해시


def section_for(word: str, inputs: Inputs, matches: dict[str, str]) -> str | None:
    """업무 표현의 첫 단어 → 업무 설명 절 이름 (제목과 같으면 그대로, 아니면 확정된 업무 매칭)."""
    if inputs.explanation is None:
        return None
    if word in inputs.explanation.sections:
        return word
    sec = matches.get(word)
    return sec if sec in inputs.explanation.sections else None


def business_match_source(expr: str, inputs: Inputs) -> str:
    titles = inputs.explanation.titles_hash if inputs.explanation else ""
    return text_hash(f"match|{expr}|{titles}")


def base_date_rule_source(name: str, inputs: Inputs, matches: dict[str, str]) -> str | None:
    sec = section_for(business_word(name), inputs, matches)
    if sec is None:
        return None
    return text_hash(f"base|{name}|{inputs.explanation.sections[sec].text_hash}")


def run_rule_source(job: Job, inputs: Inputs, matches: dict[str, str]) -> str:
    """JOB 행 해시 + JOB 설명·규칙에 나오는 업무 절 해시."""
    parts = [f"run|{job.job_id}|{job.row_hash}"]
    if inputs.explanation is not None:
        for name, sec in sorted(inputs.explanation.sections.items()):
            if name in job.rule or name in job.description:
                parts.append(f"{name}:{sec.text_hash}")
        c = classify_rule(job.rule)
        if c.parts and c.parts.business_expr:
            sec = section_for(business_word(c.parts.business_expr), inputs, matches)
            if sec:
                parts.append(f"{sec}:{inputs.explanation.sections[sec].text_hash}")
    return text_hash("|".join(sorted(set(parts))))


# ================================================================ 확정값 저장소


@dataclass
class LoadResult:
    confirmed: Confirmed                       # 지금 유효한 확정값만
    invalidated: list[tuple[str, str]] = field(default_factory=list)   # (종류, 키) 근거가 바뀌어 풀린 값
    corrupt: bool = False                      # 파일이 깨져 전체 미확정 처리됨


class ConfirmedStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / CONFIRMED_FILE

    # ---------- 읽기

    def raw(self) -> tuple[dict, bool]:
        data, corrupt = _read_json(self.path)
        if data is None:
            return self._empty(), corrupt
        if data.get("version") != SCHEMA_VERSION or not all(isinstance(data.get(k), dict) for k in KINDS):
            return self._empty(), True
        for kind in KINDS:
            for key, item in data[kind].items():
                if not isinstance(item, dict) or "source_hash" not in item or "value" not in item:
                    return self._empty(), True
        return data, False

    def load(self, inputs: Inputs) -> LoadResult:
        """근거 해시가 지금 입력과 같은 값만 돌려준다."""
        data, corrupt = self.raw()
        result = LoadResult(Confirmed(), corrupt=corrupt)
        matches_all = {k: v["value"] for k, v in data["business_matches"].items()}

        for expr, item in data["business_matches"].items():
            if item["source_hash"] == business_match_source(expr, inputs):
                result.confirmed.business_matches[expr] = item["value"]
            else:
                result.invalidated.append(("business_matches", expr))
        matches = result.confirmed.business_matches

        for name, item in data["base_date_rules"].items():
            src = base_date_rule_source(name, inputs, matches)
            if src is not None and item["source_hash"] == src:
                result.confirmed.base_date_rules[name] = item["value"]
            else:
                result.invalidated.append(("base_date_rules", name))

        for job_id, item in data["run_rules"].items():
            job = inputs.job(job_id)
            if job is None:
                continue        # 규칙 파일에서 사라진 JOB의 확정값은 무시
            if item["source_hash"] == run_rule_source(job, inputs, matches_all | matches):
                result.confirmed.run_rules[job_id] = list(item["value"])
            else:
                result.invalidated.append(("run_rules", job_id))
        return result

    # ---------- 쓰기 (항목마다 원자적으로)

    def confirm(self, kind: str, key: str, value: Any, source_hash: str) -> Finding | None:
        """한 항목을 확정 저장한다. 실패하면 CONFIRM_SAVE_FAILED Finding을 돌려준다."""
        if kind not in KINDS:
            raise ValueError(kind)
        try:
            data, _ = self.raw()
            data[kind][key] = {"value": value, "source_hash": source_hash}
            atomic_write_json(self.path, data)
        except OSError as exc:
            return Finding(Code.CONFIRM_SAVE_FAILED, f"{key} 확정값을 저장하지 못했습니다: {exc}", job_id=key)
        return None

    def confirm_many(self, items: list[tuple[str, str, Any, str]]) -> list[Finding]:
        """여러 항목을 각각 따로 저장한다. 실패한 항목만 Finding으로 돌려준다."""
        return [f for item in items if (f := self.confirm(*item)) is not None]

    def remove(self, kind: str, key: str) -> None:
        data, _ = self.raw()
        if data[kind].pop(key, None) is not None:
            atomic_write_json(self.path, data)

    @staticmethod
    def _empty() -> dict:
        return {"version": SCHEMA_VERSION, **{k: {} for k in KINDS}}


# ================================================================ 대기 제안 저장소


class PendingStore:
    """확정 전 AI 제안. kind·target·source_hash 단위. 계산에는 쓰지 않는다."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / PENDING_FILE

    @staticmethod
    def key(kind: str, target: str, source_hash: str) -> str:
        return f"{kind}|{target}|{source_hash}"

    def _all(self) -> dict:
        data, corrupt = _read_json(self.path)
        if corrupt or data is None or not isinstance(data.get("items"), dict):
            return {}
        return data["items"]

    def get(self, kind: str, target: str, source_hash: str) -> dict | None:
        return self._all().get(self.key(kind, target, source_hash))

    def put(self, kind: str, target: str, source_hash: str, proposal: dict) -> None:
        items = {k: v for k, v in self._all().items() if not k.startswith(f"{kind}|{target}|")}
        items[self.key(kind, target, source_hash)] = proposal
        atomic_write_json(self.path, {"items": items})

    def discard(self, kind: str, target: str) -> None:
        """확정·거부되었거나 근거가 바뀌었을 때 그 대상의 제안을 모두 지운다."""
        items = self._all()
        kept = {k: v for k, v in items.items() if not k.startswith(f"{kind}|{target}|")}
        if len(kept) != len(items):
            atomic_write_json(self.path, {"items": kept})

    def all(self) -> dict:
        return self._all()
