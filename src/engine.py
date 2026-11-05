"""계산 엔진 (02단계). 날짜는 이 모듈만 계산한다.

흐름: 규칙 문법 판정 → 기준일 규칙으로 기준일(D) 계산 → D 연산으로 수행일 → 등록일 규칙(16:30 기준)
→ 앞 달·대상 월·다음 달 수행분 중 등록일이 대상 월인 것만 반환.

AI가 확정해 준 값(정규화된 규칙, 기준일 규칙, 업무 매칭)은 Confirmed로 받는다. 저장·무효화는 03단계 몫이다.
"""
from __future__ import annotations

import calendar as _cal
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from src.models import Code, Finding
from src.parsing import Calendar, Inputs, RuleParts, split_rule

WEEKDAYS = "월화수목금토일"
CUTOFF = (16, 30)

# ================================================================ 날짜 도구


class CalendarMissing(Exception):
    def __init__(self, year_month: str) -> None:
        super().__init__(year_month)
        self.year_month = year_month


def ym_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def ym_shift(year_month: str, months: int) -> str:
    y, m = map(int, year_month.split("-"))
    idx = y * 12 + (m - 1) + months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def last_day(year_month: str) -> date:
    y, m = map(int, year_month.split("-"))
    return date(y, m, _cal.monthrange(y, m)[1])


class BizDays:
    """영업일 계산. 달력에 없는 달의 날짜를 판정하려 하면 CalendarMissing."""

    def __init__(self, cal: Calendar) -> None:
        self.cal = cal

    def is_bd(self, d: date) -> bool:
        ym = ym_of(d)
        if not self.cal.has_month(ym):
            raise CalendarMissing(ym)
        return d.weekday() < 5 and not self.cal.is_holiday(d)

    def next_bd(self, d: date) -> date:
        """d가 영업일이면 d, 아니면 다음 영업일 (D영업일)."""
        while not self.is_bd(d):
            d += timedelta(days=1)
        return d

    def prev_bd(self, d: date) -> date:
        while not self.is_bd(d):
            d -= timedelta(days=1)
        return d

    def shift(self, d: date, n: int) -> date:
        """d를 세지 않고 영업일 n개만큼 이동 (n<0이면 앞으로)."""
        step = 1 if n > 0 else -1
        for _ in range(abs(n)):
            d += timedelta(days=step)
            while not self.is_bd(d):
                d += timedelta(days=step)
        return d


def registration_date(run: date, start_time: str) -> date:
    """등록일 규칙: 16:30~23:59 시작 → 당일, 00:00~16:29 시작 → 달력 기준 전날."""
    h, m = map(int, start_time.split(":"))
    return run if (h, m) >= CUTOFF else run - timedelta(days=1)


# ================================================================ 월 기준 규칙

_MONTH_DAY = re.compile(r"^매월 (\d+)일$")
_NTH_WEEKDAY = re.compile(r"^(\d+)째 주 ([월화수목금토일])요일$")


def month_rule_dates(rule: str, year_month: str, bd: BizDays) -> list[date]:
    """표준 월 기준 규칙의 그 달 날짜 목록."""
    first = date(*map(int, year_month.split("-")), 1)
    end = last_day(year_month)
    if (m := _MONTH_DAY.match(rule)):
        return [first.replace(day=min(int(m.group(1)), end.day))]
    if rule == "말일":
        return [end]
    if rule == "첫 영업일":
        return [bd.next_bd(first)]
    if rule == "마지막 영업일":
        return [bd.prev_bd(end)]
    if (m := _NTH_WEEKDAY.match(rule)):
        n, wd = int(m.group(1)), WEEKDAYS.index(m.group(2))
        d = first + timedelta(days=(wd - first.weekday()) % 7) + timedelta(weeks=n - 1)
        return [d] if d.month == first.month else []
    days = [first + timedelta(days=i) for i in range(end.day)]
    if rule == "매일":
        return days
    if rule == "매 영업일":
        return [d for d in days if bd.is_bd(d)]
    raise ValueError(f"표준 월 기준 규칙이 아님: {rule}")


# ================================================================ 문법 판정

STANDARD, NORMALIZE, REJECT = "standard", "normalize", "reject"

_STD_MONTH = [_MONTH_DAY, re.compile(r"^(말일|첫 영업일|마지막 영업일|매일|매 영업일)$"), _NTH_WEEKDAY]
_STD_OPS = [
    (re.compile(r"^D일$"), "day", None),
    (re.compile(r"^D([+-])(\d+)일$"), "day", 1),
    (re.compile(r"^D영업일$"), "bd", None),
    (re.compile(r"^D([+-])(\d+) 영업일$"), "bd", 1),
    (re.compile(r"^D일 ([+-]) (\d+)영업일$"), "raw_bd", 1),
    (re.compile(r"^영업일 D일 ([+-]) (\d+)일$"), "bd_day", 1),
]


@dataclass(frozen=True)
class DOp:
    kind: str     # day | bd | raw_bd | bd_day
    n: int

    def apply(self, d: date, bd: BizDays) -> date:
        if self.kind == "day":
            return d + timedelta(days=self.n)
        if self.kind == "bd":
            return bd.shift(bd.next_bd(d), self.n)
        if self.kind == "raw_bd":
            return bd.shift(d, self.n)
        if self.kind == "bd_day":
            return bd.next_bd(d) + timedelta(days=self.n)
        raise ValueError(self.kind)


def parse_d_op(text: str) -> DOp | None:
    for pattern, kind, sign_group in _STD_OPS:
        m = pattern.match(text)
        if m:
            n = 0 if sign_group is None else int(m.group(2)) * (1 if m.group(1) == "+" else -1)
            return DOp(kind, n)
    return None


@dataclass(frozen=True)
class Classified:
    status: str                  # standard | normalize | reject
    parts: RuleParts | None
    reason: str = ""
    op: DOp | None = None


def _month_numbers_out_of_range(text: str) -> str | None:
    if (m := re.search(r"매월\s*(\d+)\s*일", text)) and not 1 <= int(m.group(1)) <= 31:
        return f"매월 {m.group(1)}일은 1~31 범위를 벗어납니다."
    if (m := re.search(r"(\d+)째\s*주", text)) and not 1 <= int(m.group(1)) <= 5:
        return f"{m.group(1)}째 주는 1~5 범위를 벗어납니다."
    return None


def classify_rule(rule: str) -> Classified:
    """표준(바로 계산) / 정규화 대상(AI 제안 후 확인) / 거부(MD_SYNTAX_ERROR)."""
    parts = split_rule(rule)
    if parts is None:
        return Classified(NORMALIZE, None, "규칙 칸이 비어 있음 (JOB 설명으로 판단)")
    if (reason := _month_numbers_out_of_range(parts.raw)):
        return Classified(REJECT, parts, reason)
    if parts.d_op is not None and not parts.business_expr:
        return Classified(REJECT, parts, "D 연산 앞에 업무 표현이 없습니다.")
    if parts.d_op is None:
        if any(p.match(parts.raw) for p in _STD_MONTH):
            return Classified(STANDARD, parts)
        return Classified(NORMALIZE, parts, "표준 월 기준 규칙과 표현이 다름")
    op = parse_d_op(parts.d_op)
    if op is None:
        return Classified(NORMALIZE, parts, f"D 연산 '{parts.d_op}'의 표현이 표준과 다름")
    if parts.base_text.startswith("매월 "):
        return Classified(NORMALIZE, parts, "'매월' 접두어가 붙은 기준일 표현")
    if any(p.match(parts.business_expr) for p in _STD_MONTH):
        return Classified(NORMALIZE, parts, "월 기준 규칙에 D 연산이 붙음")
    return Classified(STANDARD, parts, op=op)


# ================================================================ 기준일 규칙


class RuleSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class BaseDateRule:
    name: str
    start: str                      # 월 기준 규칙 또는 참조할 기준일 이름
    start_is_ref: bool
    holiday: str | None = None      # next | prev | None
    overlap_next: str | None = None  # 이 기준일 묶음과 같으면 다음 영업일
    exclude_group: str | None = None  # 앞선 이 묶음과 겹치면 수행 안 함


def parse_base_date_rule(name: str, text: str) -> BaseDateRule:
    """예: '매월 5일, 휴일이면 다음 영업일, 앞선 자동이체 응당일과 겹치면 수행 안 함'."""
    pieces = [p.strip() for p in text.split(",") if p.strip()]
    if not pieces:
        raise RuleSyntaxError(f"{name}: 기준일 규칙이 비어 있습니다.")
    head, *rest = pieces
    if head.endswith("과 같음") or head.endswith("와 같음"):
        start, is_ref = head[:-4].strip(), True
    else:
        if not any(p.match(head) for p in _STD_MONTH) or head in ("매일", "매 영업일"):
            raise RuleSyntaxError(f"{name}: 출발 날짜 '{head}'는 표준 월 기준 규칙이 아닙니다.")
        start, is_ref = head, False
    holiday = overlap_next = exclude_group = None
    for piece in rest:
        if piece == "휴일이면 다음 영업일":
            holiday = "next"
        elif piece == "휴일이면 이전 영업일":
            holiday = "prev"
        elif (m := re.fullmatch(r"앞선 (.+?)[과와] 겹치면 수행 안 함", piece)):
            exclude_group = m.group(1)
        elif (m := re.fullmatch(r"(.+?)[과와] 같으면 다음 영업일", piece)):
            overlap_next = m.group(1)
        else:
            raise RuleSyntaxError(f"{name}: 알 수 없는 보정 '{piece}'")
    return BaseDateRule(name, start, is_ref, holiday, overlap_next, exclude_group)


@dataclass(frozen=True)
class Occurrence:
    name: str
    origin: str          # 출발 날짜가 속한 달 (YYYY-MM)
    original: date       # 보정 전 출발 날짜
    date: date           # 보정 후 기준일 D
    excluded: bool = False


class BaseDates:
    """확정된 기준일 규칙으로 달마다 기준일(D)을 계산한다."""

    def __init__(self, rules: dict[str, BaseDateRule], bd: BizDays) -> None:
        self.rules = rules
        self.bd = bd
        self._memo: dict[tuple[str, str], Occurrence | None] = {}
        self._raw_memo: dict[tuple[str, str], Occurrence | None] = {}
        self._stack: set[tuple[str, str]] = set()

    def group(self, spec: str) -> list[str]:
        """묶음 이름 → 기준일 이름 목록. '자동이체 응당일' → '자동이체 *일자 응당일' 전부."""
        if spec in self.rules:
            return [spec]
        words = spec.split()
        if len(words) >= 2:
            head, tail = words[0], " ".join(words[1:])
            return [n for n in self.rules if n.startswith(head + " ") and n.endswith(" " + tail)]
        return []

    def check_cycles(self) -> list[str]:
        """참조 순환이 있는 기준일 이름 목록."""
        bad = []
        for name in self.rules:
            seen, cur = set(), name
            while cur in self.rules and self.rules[cur].start_is_ref:
                if cur in seen:
                    bad.append(name)
                    break
                seen.add(cur)
                cur = self.rules[cur].start
        return bad

    def _raw(self, name: str, origin: str) -> Occurrence | None:
        """출발 날짜 + 휴일 보정 + 겹침 보정 (겹침 제외 전)."""
        key = (name, origin)
        if key in self._raw_memo:
            return self._raw_memo[key]
        if key in self._stack:
            raise RuleSyntaxError(f"기준일 '{name}' 규칙이 순환 참조합니다.")
        self._stack.add(key)
        try:
            rule = self.rules[name]
            if rule.start_is_ref:
                ref = self.occurrence(rule.start, origin)
                if ref is None:
                    result = None
                else:
                    result = Occurrence(name, origin, ref.original, ref.date, ref.excluded)
            else:
                starts = month_rule_dates(rule.start, origin, self.bd)
                if not starts:
                    result = None
                else:
                    original = d = starts[0]
                    if rule.holiday == "next":
                        d = self.bd.next_bd(d)
                    elif rule.holiday == "prev":
                        d = self.bd.prev_bd(d)
                    if rule.overlap_next:
                        taken = self._group_dates(rule.overlap_next, origin, exclude_self=name)
                        while d in taken:
                            d = self.bd.shift(d, 1)
                    result = Occurrence(name, origin, original, d)
        finally:
            self._stack.discard(key)
        self._raw_memo[key] = result
        return result

    def _group_dates(self, spec: str, origin: str, exclude_self: str) -> set[date]:
        dates: set[date] = set()
        for o in (ym_shift(origin, -1), origin):
            for n in self.group(spec):
                if n == exclude_self:
                    continue
                try:
                    occ = self.occurrence(n, o)
                except CalendarMissing:
                    continue   # 앞 달 정보가 없으면 그 달과의 겹침은 판단하지 않음
                if occ and not occ.excluded:
                    dates.add(occ.date)
        return dates

    def occurrence(self, name: str, origin: str) -> Occurrence | None:
        """origin 달에서 출발한 기준일 (겹침 제외 반영). 그 달에 없으면 None."""
        key = (name, origin)
        if key in self._memo:
            return self._memo[key]
        raw = self._raw(name, origin)
        if raw is not None and not raw.excluded:
            rule = self.rules[name]
            if rule.exclude_group:
                for o in (ym_shift(origin, -1), origin):
                    for other in self.group(rule.exclude_group):
                        if other == name:
                            continue
                        try:
                            cand = self._raw(other, o)
                        except CalendarMissing:
                            continue
                        if cand and cand.date == raw.date and cand.original < raw.original:
                            raw = Occurrence(raw.name, raw.origin, raw.original, raw.date, excluded=True)
                            break
                    if raw.excluded:
                        break
        self._memo[key] = raw
        return raw


# ================================================================ JOB 규칙 → 기준일 이름


def expand_base_names(expr: str, every: bool, base: BaseDates) -> list[str]:
    """'자동이체 5,15,25일자 응당일' → 3개, '매 ...' → 묶음 전체, 그 외 → 그대로 1개."""
    if every:
        return base.group(expr)
    words = expr.split()
    for i, w in enumerate(words):
        if "," in w:
            items = w.split(",")
            suffix = re.search(r"(일자|일)$", items[-1])
            suf = suffix.group(1) if suffix else ""
            names = []
            for it in items:
                it = it if it.endswith(suf) or not suf else it + suf
                names.append(" ".join(words[:i] + [it] + words[i + 1:]))
            return names
    return [expr]


def business_word(expr: str) -> str:
    """업무 매칭에 쓰는 업무 표현 (첫 단어). 예: '자동이체 5일자 응당일' → '자동이체', '마감일' → '마감일'."""
    return expr.split()[0]


# ================================================================ 일정 계산


@dataclass
class Confirmed:
    """사용자가 확정한 값 (저장은 03단계)."""

    run_rules: dict[str, list[str]] = field(default_factory=dict)      # job_id → 표준 규칙 목록
    base_date_rules: dict[str, str] = field(default_factory=dict)      # 기준일 이름 → 기준일 규칙 문장
    business_matches: dict[str, str] = field(default_factory=dict)     # 업무 표현 → 업무 설명 절 이름


@dataclass(frozen=True)
class ScheduleEntry:
    job_id: str
    run_date: date
    start_time: str
    registration_date: date


@dataclass
class ScheduleResult:
    year_month: str
    entries: list[ScheduleEntry]
    unconfirmed: dict[str, list[Code]]   # job_id → 미확정 코드 (매트릭스에서 해당 행만 "미확정")
    findings: list[Finding]              # 매트릭스를 막는 오류 (있으면 entries는 비어 있음)

    @property
    def blocked(self) -> bool:
        return bool(self.findings)

    def dates_of(self, job_id: str) -> list[date]:
        return sorted({e.registration_date for e in self.entries if e.job_id == job_id})

    def jobs_on(self, day: date) -> list[str]:
        return sorted({e.job_id for e in self.entries if e.registration_date == day})


def required_months(year_month: str) -> list[str]:
    return [ym_shift(year_month, -1), year_month, ym_shift(year_month, 1)]


def _resolve_rules(job, confirmed: Confirmed) -> tuple[list[Classified], list[Code], list[Finding]]:
    """JOB의 계산용 표준 규칙 목록. 미확정이면 코드, 거부면 MD_SYNTAX_ERROR."""
    c = classify_rule(job.rule)
    if c.status == REJECT:
        return [], [], [Finding(Code.MD_SYNTAX_ERROR, f"{job.job_id}의 수행일 규칙 '{job.rule}': {c.reason}",
                                job_id=job.job_id)]
    if c.status == STANDARD:
        return [c], [], []
    texts = confirmed.run_rules.get(job.job_id)
    if not texts:
        return [], [Code.JOB_RULE_UNCONFIRMED], []
    out = []
    for t in texts:
        cc = classify_rule(t)
        if cc.status != STANDARD:
            return [], [], [Finding(Code.MD_SYNTAX_ERROR, f"{job.job_id}의 확정 규칙 '{t}'이 표준 문법이 아닙니다.",
                                    job_id=job.job_id)]
        out.append(cc)
    return out, [], []


def compute_schedule(inputs: Inputs, confirmed: Confirmed, year_month: str) -> ScheduleResult:
    """대상 월의 등록 일정. 앞 달·대상 월·다음 달 수행분 중 등록일이 대상 월인 것만 반환한다."""
    blocking = [f for f in inputs.findings if f.code in (Code.MD_FILE_MISSING, Code.MD_SYNTAX_ERROR,
                                                          Code.START_TIME_INVALID)]
    missing = [ym for ym in required_months(year_month) if not inputs.calendar.has_month(ym)]
    blocking += [Finding(Code.CALENDAR_MISSING, f"{ym} 달력이 business-calendar.md에 없습니다.", missing_month=ym)
                 for ym in missing]

    bd = BizDays(inputs.calendar)
    try:
        rules = {n: parse_base_date_rule(n, t) for n, t in confirmed.base_date_rules.items()}
    except RuleSyntaxError as exc:
        blocking.append(Finding(Code.MD_SYNTAX_ERROR, str(exc)))
        rules = {}
    base = BaseDates(rules, bd)
    for name in base.check_cycles():
        blocking.append(Finding(Code.MD_SYNTAX_ERROR, f"기준일 '{name}' 규칙이 순환 참조합니다."))

    plans: dict[str, list[Classified]] = {}
    unconfirmed: dict[str, list[Code]] = {}
    needs_explanation = False
    titles = set(inputs.explanation.titles) if inputs.explanation else set()
    for job in inputs.jobs:
        classified, codes, errs = _resolve_rules(job, confirmed)
        blocking += errs
        if not job.rule.strip() or any(c.parts and c.parts.d_op for c in classified) \
                or (classify_rule(job.rule).parts and classify_rule(job.rule).parts.d_op):
            needs_explanation = True
        for c in classified:
            if c.parts and c.parts.d_op:
                word = business_word(c.parts.business_expr)
                if word not in titles and word not in confirmed.business_matches \
                        and c.parts.business_expr not in confirmed.business_matches:
                    codes.append(Code.BUSINESS_MATCH_UNCONFIRMED)
                names = expand_base_names(c.parts.business_expr, c.parts.every, base)
                if not names or any(n not in rules for n in names):
                    codes.append(Code.BASE_DATE_UNCONFIRMED)
        if codes:
            unconfirmed[job.job_id] = sorted(set(codes), key=lambda x: x.value)
        else:
            plans[job.job_id] = classified
    if needs_explanation and inputs.explanation is None:
        blocking.append(Finding(Code.MD_FILE_MISSING, "business-explanation.md 파일이 필요하지만 없거나 비어 있습니다.",
                                file="business-explanation.md"))

    if blocking:
        return ScheduleResult(year_month, [], unconfirmed, _dedupe(blocking))

    window = set(required_months(year_month))
    entries: set[ScheduleEntry] = set()
    try:
        for job in inputs.jobs:
            for c in plans.get(job.job_id, []):
                for run in _runs(c, year_month, base, bd, window):
                    reg = registration_date(run, job.start_time)
                    if ym_of(reg) == year_month:
                        entries.add(ScheduleEntry(job.job_id, run, job.start_time, reg))
    except CalendarMissing as exc:
        return ScheduleResult(year_month, [], unconfirmed, [Finding(
            Code.CALENDAR_MISSING, f"{exc.year_month} 달력이 business-calendar.md에 없습니다.",
            missing_month=exc.year_month)])
    except RuleSyntaxError as exc:
        return ScheduleResult(year_month, [], unconfirmed, [Finding(Code.MD_SYNTAX_ERROR, str(exc))])
    ordered = sorted(entries, key=lambda e: (e.registration_date, e.job_id, e.run_date))
    return ScheduleResult(year_month, ordered, unconfirmed, [])


def _runs(c: Classified, year_month: str, base: BaseDates, bd: BizDays, window: set[str]) -> list[date]:
    """한 규칙의 수행일 후보. 대상 월에 영향을 줄 수 없는 바깥 달의 달력 부족은 건너뛴다."""
    runs: list[date] = []
    if c.parts.d_op is None:
        # 등록일은 수행일 당일 또는 전날 → 대상 월과 다음 달 수행분만 보면 된다
        for ym in (year_month, ym_shift(year_month, 1)):
            runs += month_rule_dates(c.parts.raw, ym, bd)
        return runs
    names = expand_base_names(c.parts.business_expr, c.parts.every, base)
    for origin in required_months(year_month):
        for name in names:
            try:
                occ = base.occurrence(name, origin)
                if occ is None or occ.excluded:
                    continue
                runs.append(c.op.apply(occ.date, bd))
            except CalendarMissing as exc:
                if exc.year_month in window:
                    raise
                continue   # 창 밖의 달(M-2 이전, M+2 이후)은 대상 월 등록일에 영향을 주지 않음
    return runs


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in findings:
        key = (f.code, f.message)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
