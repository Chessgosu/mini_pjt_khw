"""입력 MD 3종 파싱 (01단계).

- batch-schedule.md: JOB 표
- business-explanation.md: "## 업무명" 절
- business-calendar.md: "## YYYY-MM" 절과 휴일 목록

파일은 호출할 때마다 다시 읽는다(캐시하지 않음). 오류는 예외 대신 Finding 목록으로 돌려준다.
규칙 문자열은 여기서 글자 단위로만 나누고, 문법 판정은 계산 엔진(02단계)에서 한다.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.models import Code, Finding

SCHEDULE_FILE = "batch-schedule.md"
EXPLANATION_FILE = "business-explanation.md"
CALENDAR_FILE = "business-calendar.md"

REQUIRED_COLUMNS = ("JOB ID", "수행일 규칙", "시작 시각")
OPTIONAL_COLUMNS = ("JOB 이름", "JOB 설명")
START_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
MONTH_HEADING_RE = re.compile(r"^##\s+(\d{4})-(\d{2})\s*$")


# ---------------------------------------------------------------- 공통 도구

def normalize_text(text: str) -> str:
    """공백·줄바꿈 차이를 없앤다 (연속 공백을 한 칸으로, 앞뒤 공백 제거)."""
    return re.sub(r"\s+", " ", text).strip()


def text_hash(text: str) -> str:
    """정규화한 뒤 SHA-256. 공백·줄바꿈만 바뀌면 해시가 같다."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def _read(path: Path, findings: list[Finding], required: bool = True) -> str | None:
    """파일을 읽는다. 없음·빈 파일·읽기 불가는 MD_FILE_MISSING (required일 때만 기록)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if required:
            findings.append(Finding(Code.MD_FILE_MISSING, f"{path.name} 파일이 없습니다.", file=path.name))
        return None
    except (OSError, UnicodeDecodeError) as exc:
        if required:
            findings.append(Finding(Code.MD_FILE_MISSING, f"{path.name} 파일을 읽을 수 없습니다: {exc}", file=path.name))
        return None
    if not text.strip():
        if required:
            findings.append(Finding(Code.MD_FILE_MISSING, f"{path.name} 파일이 비어 있습니다.", file=path.name))
        return None
    return text


def _split_row(line: str) -> list[str]:
    """마크다운 표 한 줄을 칸 목록으로 나눈다. '| B202|'처럼 공백이 없어도 된다."""
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _is_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c) and any(cells)


# ---------------------------------------------------------------- batch-schedule.md

@dataclass(frozen=True)
class Job:
    job_id: str
    name: str
    rule: str                 # 비어 있을 수 있음
    start_time: str | None    # 유효한 HH:MM만, 아니면 None
    description: str
    row_hash: str


def parse_schedule(text: str) -> tuple[list[Job], list[Finding]]:
    findings: list[Finding] = []
    lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    if not lines:
        return [], [Finding(Code.MD_SYNTAX_ERROR, f"{SCHEDULE_FILE}에 JOB 표가 없습니다.", file=SCHEDULE_FILE)]

    header = _split_row(lines[0])
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        return [], [Finding(Code.MD_SYNTAX_ERROR, f"{SCHEDULE_FILE}에 필수 열이 없습니다: {', '.join(missing)}",
                            file=SCHEDULE_FILE)]
    col = {name: header.index(name) for name in (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS) if name in header}

    jobs: list[Job] = []
    seen: set[str] = set()
    for line in lines[1:]:
        cells = _split_row(line)
        if _is_separator(cells):
            continue
        cells += [""] * (len(header) - len(cells))

        def cell(name: str) -> str:
            return cells[col[name]] if name in col else ""

        job_id = cell("JOB ID")
        if not job_id:
            findings.append(Finding(Code.MD_SYNTAX_ERROR, f"{SCHEDULE_FILE}에 JOB ID가 빈 행이 있습니다.",
                                    file=SCHEDULE_FILE))
            continue
        if job_id in seen:
            findings.append(Finding(Code.MD_SYNTAX_ERROR, f"{SCHEDULE_FILE}에 JOB ID {job_id}가 중복됩니다.",
                                    job_id=job_id, file=SCHEDULE_FILE))
            continue
        seen.add(job_id)

        raw_time = cell("시작 시각")
        start_time = raw_time if START_TIME_RE.fullmatch(raw_time) else None
        if start_time is None:
            shown = raw_time or "(빈 칸)"
            findings.append(Finding(Code.START_TIME_INVALID,
                                    f"{job_id}의 시작 시각 '{shown}'이 00:00~23:59의 HH:MM 형식이 아닙니다.",
                                    job_id=job_id, file=SCHEDULE_FILE))
        jobs.append(Job(job_id=job_id, name=cell("JOB 이름"), rule=cell("수행일 규칙"), start_time=start_time,
                        description=cell("JOB 설명"), row_hash=text_hash(line)))
    return jobs, findings


# ---------------------------------------------------------------- business-explanation.md

@dataclass(frozen=True)
class BusinessSection:
    name: str
    text: str
    text_hash: str


@dataclass(frozen=True)
class Explanation:
    sections: dict[str, BusinessSection]
    titles_hash: str    # "##" 제목 목록 해시 (제목 추가·삭제·변경 시 업무 매칭 재확인)

    @property
    def titles(self) -> list[str]:
        return list(self.sections)


def parse_explanation(text: str) -> tuple[Explanation, list[Finding]]:
    findings: list[Finding] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m and not line.startswith("###"):
            current = m.group(1)
            if current in sections:
                findings.append(Finding(Code.MD_SYNTAX_ERROR, f"{EXPLANATION_FILE}에 업무 '{current}' 절이 중복됩니다.",
                                        file=EXPLANATION_FILE))
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    if not sections:
        findings.append(Finding(Code.MD_SYNTAX_ERROR, f"{EXPLANATION_FILE}에 '## 업무명' 절이 없습니다.",
                                file=EXPLANATION_FILE))
    built = {name: BusinessSection(name, "\n".join(body).strip(), text_hash("\n".join(body)))
             for name, body in sections.items()}
    return Explanation(built, text_hash("\n".join(sections))), findings


# ---------------------------------------------------------------- business-calendar.md

@dataclass(frozen=True)
class CalendarMonth:
    year_month: str
    holidays: dict[date, str]
    section_hash: str


@dataclass
class Calendar:
    months: dict[str, CalendarMonth] = field(default_factory=dict)

    def has_month(self, year_month: str) -> bool:
        return year_month in self.months

    def is_holiday(self, day: date) -> bool:
        month = self.months.get(f"{day.year:04d}-{day.month:02d}")
        return bool(month and day in month.holidays)

    def merged(self, other: "Calendar") -> "Calendar":
        """다른 달력을 덧붙인 새 달력 (테스트 fixture 달력 합치기용)."""
        return Calendar({**self.months, **other.months})


def parse_calendar(text: str) -> tuple[Calendar, list[Finding]]:
    findings: list[Finding] = []
    raw: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            m = MONTH_HEADING_RE.match(line)
            if not m or not 1 <= int(m.group(2)) <= 12:
                findings.append(Finding(Code.MD_SYNTAX_ERROR,
                                        f"{CALENDAR_FILE}의 제목 '{line.strip()}'이 '## YYYY-MM' 형식이 아닙니다.",
                                        file=CALENDAR_FILE))
                current = None
                continue
            current = f"{m.group(1)}-{m.group(2)}"
            raw[current] = []
        elif current is not None:
            raw[current].append(line)

    months: dict[str, CalendarMonth] = {}
    for ym, body in raw.items():
        holidays: dict[date, str] = {}
        for line in body:
            if not line.strip().startswith("|"):
                continue
            cells = _split_row(line)
            if _is_separator(cells) or cells[0] in ("날짜", ""):
                continue
            try:
                day = date.fromisoformat(cells[0])
            except ValueError:
                findings.append(Finding(Code.MD_SYNTAX_ERROR, f"{CALENDAR_FILE} {ym}의 날짜 '{cells[0]}'가 올바르지 않습니다.",
                                        file=CALENDAR_FILE))
                continue
            if f"{day.year:04d}-{day.month:02d}" != ym:
                findings.append(Finding(Code.MD_SYNTAX_ERROR, f"{CALENDAR_FILE} {ym} 절에 다른 달 날짜 {day}가 있습니다.",
                                        file=CALENDAR_FILE))
                continue
            holidays[day] = cells[1] if len(cells) > 1 else ""
        months[ym] = CalendarMonth(ym, holidays, text_hash("\n".join(body)))
    return Calendar(months), findings


# ---------------------------------------------------------------- 전체 읽기

@dataclass
class Inputs:
    jobs: list[Job]
    explanation: Explanation | None    # 파일이 없으면 None (필요 여부는 계산 엔진이 판단)
    calendar: Calendar
    findings: list[Finding]

    def job(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.job_id == job_id), None)


def load_inputs(data_dir: Path) -> Inputs:
    """data 폴더의 MD 3개를 매번 새로 읽는다.

    batch-schedule.md와 business-calendar.md는 항상 필요하다. business-explanation.md는
    기준일 규칙이나 규칙 칸이 빈 JOB이 있을 때만 필요하므로, 없으면 explanation=None으로 두고
    필요 여부 판단은 계산 엔진에 맡긴다.
    """
    data_dir = Path(data_dir)
    findings: list[Finding] = []
    jobs: list[Job] = []
    explanation: Explanation | None = None
    calendar = Calendar()

    text = _read(data_dir / SCHEDULE_FILE, findings)
    if text is not None:
        jobs, f = parse_schedule(text)
        findings += f
    text = _read(data_dir / CALENDAR_FILE, findings)
    if text is not None:
        calendar, f = parse_calendar(text)
        findings += f
    text = _read(data_dir / EXPLANATION_FILE, findings, required=False)
    if text is not None:
        explanation, f = parse_explanation(text)
        findings += f
    return Inputs(jobs, explanation, calendar, findings)


def explanation_missing_finding(data_dir: Path) -> Finding:
    """business-explanation.md가 필요한데 없을 때 계산 엔진이 쓰는 Finding."""
    return Finding(Code.MD_FILE_MISSING, f"{EXPLANATION_FILE} 파일이 필요하지만 없거나 비어 있습니다.",
                   file=EXPLANATION_FILE)


# ---------------------------------------------------------------- 규칙 문자열 나누기

# D 연산 패턴. 순서가 중요하다: "영업일 D일 ± N일"을 먼저 보고, 나머지는 짧은 것부터 끝에 맞춘다.
_D_OP_PATTERNS = [
    r"영업일\s+D일\s*[+-]\s*\d+\s*일",        # 영업일 D일 ± N일
    r"D일\s*[+-]\s*\d+\s*영업일",             # D일 ± N영업일
    r"D일\s*[+-]\s*\d+\s*일",                 # D일 ± N일 (정규화 대상: D±N일)
    r"D\s*[+-]\s*\d+\s*(?:영업일|일)",        # D±N일, D±N 영업일, "D+2 일"
    r"D영업일",
    r"D일",
]
_D_OP_RE = re.compile(r"(?P<op>" + "|".join(_D_OP_PATTERNS) + r")\s*$")


@dataclass(frozen=True)
class RuleParts:
    """규칙 문자열을 글자 단위로 나눈 결과. 문법이 맞는지는 판정하지 않는다."""

    raw: str
    base_text: str | None    # D 연산 앞부분 원문 (D 연산이 없으면 None)
    d_op: str | None         # D 연산 원문 (예 "D+2 영업일")
    every: bool              # "매 <업무> ..." 형태 (그 업무의 모든 기준일)
    business_expr: str | None  # base_text에서 "매월"/"매" 접두어를 뺀 업무 표현 (예 "마감일")

    @property
    def is_month_rule(self) -> bool:
        """D 연산이 없는 규칙 (매월 N일, 첫 영업일, 매일 등)."""
        return self.d_op is None


def split_rule(rule: str) -> RuleParts | None:
    """규칙 문자열을 업무 표현과 D 연산으로 나눈다. 빈 규칙이면 None."""
    raw = normalize_text(rule)
    if not raw:
        return None
    m = _D_OP_RE.search(raw)
    if not m:
        return RuleParts(raw, None, None, False, None)
    if m.start() == 0:
        # "D+1 영업일"처럼 업무 표현 없이 D 연산만 있음 → 02단계에서 MD_SYNTAX_ERROR로 판정
        return RuleParts(raw, None, normalize_text(m.group("op")), False, None)
    base = raw[: m.start()].strip()
    d_op = normalize_text(m.group("op"))
    every = False
    expr = base
    if expr.startswith("매월 "):
        expr = expr[len("매월 "):].strip()
    elif expr.startswith("매 "):
        expr = expr[len("매 "):].strip()
        every = True
    return RuleParts(raw, base, d_op, every, expr or None)
