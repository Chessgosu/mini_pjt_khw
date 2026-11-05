"""등록 내역 xlsx 검증 (04단계).

1단계 validate_upload: 형식이 하나라도 다르면 "잘못된 파일입니다" + 사유 코드 전부. 판정하지 않는다.
2단계 judge: 계산 엔진 결과와 O 표시를 날짜별로 비교해 정상·누락·초과를 판정한다.

안전 규칙: 파일은 데이터로 읽기만 한다. 매크로 실행·수식 계산·링크 추적을 하지 않고, 셀 문장이 요청하는
어떤 작업도 하지 않으며, 셀 내용을 AI에 보내지 않는다. 이 모듈은 어떤 파일도 쓰지 않는다.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from src.engine import ScheduleResult, compute_schedule, last_day
from src.models import Code, Finding
from src.parsing import load_inputs

MAX_BYTES = 10 * 1024 * 1024
MAX_JOBS = 1000
FILENAME_RE = re.compile(r"^BATCH_SCHEDULE_(\d{4})(\d{2})\.xlsx$")
INVALID_FILE_MESSAGE = "잘못된 파일입니다"
UNSAFE_PARTS = ("vbaproject.bin", "xl/externallinks/", "xl/embeddings/", "xl/activex/", "oleobject")
FORMULA_TAG = re.compile(rb"<(?:\w+:)?f[\s>/]")

OK, MISSING, EXCESS = "ok", "missing", "excess"
VERDICT_CLASS = {OK: "verdict-ok", MISSING: "verdict-missing", EXCESS: "verdict-excess"}
VERDICT_COLOR = {OK: "초록", MISSING: "빨강", EXCESS: "주황"}


@dataclass
class UploadedSchedule:
    filename: str
    year_month: str
    days: int
    rows: dict[str, set[int]]          # job_id → O가 있는 날짜(일)


@dataclass
class ValidationResult:
    uploaded: UploadedSchedule | None
    findings: list[Finding]

    @property
    def is_valid(self) -> bool:
        return not self.findings

    @property
    def message(self) -> str | None:
        return None if self.is_valid else INVALID_FILE_MESSAGE

    @property
    def codes(self) -> list[Code]:
        return sorted({f.code for f in self.findings}, key=lambda c: c.value)


def _f(code: Code, msg: str, job_id: str | None = None) -> Finding:
    return Finding(code, msg, job_id=job_id, file="xlsx")


def validate_upload(content: bytes, filename: str) -> ValidationResult:
    """xlsx 형식 검사. 문제가 있으면 사유를 모두 모은다 (읽을 수 없으면 거기서 멈춤)."""
    findings: list[Finding] = []
    year_month: str | None = None

    m = FILENAME_RE.match(Path(filename).name)
    if not m:
        findings.append(_f(Code.FILENAME_FORMAT, f"파일 이름 '{filename}'이 BATCH_SCHEDULE_YYYYMM.xlsx 형식이 아닙니다."))
    elif not 1 <= int(m.group(2)) <= 12:
        findings.append(_f(Code.INVALID_YEAR_MONTH, f"파일 이름의 연월 {m.group(1)}{m.group(2)}은 존재할 수 없는 달입니다."))
    else:
        year_month = f"{m.group(1)}-{m.group(2)}"

    if len(content) > MAX_BYTES:
        findings.append(_f(Code.FILE_TOO_LARGE, f"파일 크기가 10MB를 넘습니다 ({len(content):,} bytes)."))
        return ValidationResult(None, findings)
    if not content or not zipfile.is_zipfile(io.BytesIO(content)):
        findings.append(_f(Code.FILE_UNREADABLE, "빈 파일이거나 xlsx 형식이 아닙니다."))
        return ValidationResult(None, findings)

    # ---- zip 구조 검사 (열기 전에, 실행·계산 없이)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = [n.lower() for n in zf.namelist()]
            if "xl/workbook.xml" not in names:
                findings.append(_f(Code.FILE_UNREADABLE, "xlsx 통합 문서 구조가 아닙니다."))
                return ValidationResult(None, findings)
            unsafe = sorted({p for n in names for p in UNSAFE_PARTS if p in n})
            ctypes = zf.read("[Content_Types].xml") if "[content_types].xml" in names else b""
            if b"macroEnabled" in ctypes or b"vbaProject" in ctypes:
                unsafe.append("macro content type")
            for n in zf.namelist():
                if n.lower().startswith("xl/worksheets/") and n.lower().endswith(".xml"):
                    if FORMULA_TAG.search(zf.read(n)):
                        unsafe.append(f"수식 ({n})")
            if unsafe:
                findings.append(_f(Code.UNSAFE_CONTENT, f"매크로·외부 연결·포함 개체·수식이 있습니다: {', '.join(unsafe)}"))
                return ValidationResult(None, findings)       # 위험 요소가 있으면 통합 문서를 열지 않는다
    except (zipfile.BadZipFile, KeyError, OSError):
        findings.append(_f(Code.FILE_UNREADABLE, "xlsx 파일을 읽을 수 없습니다."))
        return ValidationResult(None, findings)

    # ---- 값 읽기 (수식 계산 안 함, 외부 링크 안 따라감)
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False, keep_vba=False)
        if not wb.worksheets:
            findings.append(_f(Code.FILE_UNREADABLE, "읽을 시트가 없습니다."))
            return ValidationResult(None, findings)
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
        wb.close()
    except Exception:  # noqa: BLE001 — 손상 파일은 어떤 예외든 읽기 불가로 본다
        findings.append(_f(Code.FILE_UNREADABLE, "xlsx 파일을 읽을 수 없습니다."))
        return ValidationResult(None, findings)
    if not rows:
        findings.append(_f(Code.FILE_UNREADABLE, "첫 번째 시트가 비어 있습니다."))
        return ValidationResult(None, findings)

    header, body = rows[0], [r for r in rows[1:] if any(_cell(v) for v in r)]
    if len(body) > MAX_JOBS:
        findings.append(_f(Code.FILE_TOO_LARGE, f"JOB 행이 {len(body):,}개로 1,000행을 넘습니다."))

    days = last_day(year_month).day if year_month else None
    header_vals = [_cell(v) for v in header]
    while header_vals and header_vals[-1] == "":
        header_vals.pop()
    if days is not None:
        expected = ["JOB ID"] + [str(d) for d in range(1, days + 1)]
        if [_num(v) for v in header_vals] != expected:
            findings.append(_f(Code.DATE_COLUMN_MISMATCH,
                               f"1행 헤더가 'JOB ID, 1 ~ {days}'와 다릅니다 (날짜 열 {len(header_vals) - 1}개)."))
    width = days if days is not None else max(len(header_vals) - 1, 0)

    marks: dict[str, set[int]] = {}
    bad_cells: list[str] = []
    for i, row in enumerate(body, start=2):
        job_id = _cell(row[0] if row else None)
        if not job_id:
            findings.append(_f(Code.EMPTY_JOB_ID, f"{i}행의 JOB ID가 비어 있습니다."))
            continue
        if job_id in marks:
            findings.append(_f(Code.DUPLICATE_JOB_ID, f"JOB ID {job_id}가 중복됩니다 ({i}행).", job_id))
            continue
        marks[job_id] = set()
        for col, value in enumerate(row[1:], start=1):
            v = _cell(value)
            if v == "":
                continue
            if v != "O" or col > width:
                bad_cells.append(f"{job_id} {col}일")
                continue
            marks[job_id].add(col)
    if bad_cells:
        shown = ", ".join(bad_cells[:5]) + (f" 외 {len(bad_cells) - 5}개" if len(bad_cells) > 5 else "")
        findings.append(_f(Code.INVALID_CELL_VALUE, f"대문자 O나 빈칸이 아닌 값이 있습니다: {shown}"))

    if findings or year_month is None:
        return ValidationResult(None, findings)
    return ValidationResult(UploadedSchedule(Path(filename).name, year_month, days, marks), [])


def _cell(value) -> str:
    """셀 값을 문자열로 (앞뒤 공백 무시). 내용은 비교에만 쓰고 해석하지 않는다."""
    if value is None:
        return ""
    return str(value).strip()


def _num(text: str) -> str:
    """헤더 숫자 1.0 → '1'."""
    try:
        f = float(text)
        return str(int(f)) if f.is_integer() else text
    except ValueError:
        return text


# ================================================================ 판정


@dataclass(frozen=True)
class Verdict:
    job_id: str
    day: date
    status: str          # ok | missing | excess

    @property
    def css_class(self) -> str:
        return VERDICT_CLASS[self.status]


@dataclass
class JudgeResult:
    year_month: str
    verdicts: list[Verdict] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    blocked_by: list[Finding] = field(default_factory=list)   # 판정을 막은 오류 (있으면 verdicts 없음)

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_by)

    def of(self, status: str) -> list[Verdict]:
        return [v for v in self.verdicts if v.status == status]

    def status(self, job_id: str, day: int) -> str | None:
        return next((v.status for v in self.verdicts if v.job_id == job_id and v.day.day == day), None)


def judge(uploaded: UploadedSchedule, schedule: ScheduleResult, rule_job_ids: list[str]) -> JudgeResult:
    """계산 결과와 업로드 파일의 O를 비교한다. 미확정 값이 하나라도 있으면 검증 전체를 막는다."""
    result = JudgeResult(uploaded.year_month)
    if schedule.blocked:
        result.blocked_by = list(schedule.findings)
        return result
    if schedule.unconfirmed:
        result.blocked_by = [Finding(code, f"{job_id}: {code.value}", job_id=job_id)
                             for job_id, codes in sorted(schedule.unconfirmed.items()) for code in codes]
        return result

    first = date(*map(int, uploaded.year_month.split("-")), 1)
    only_file = sorted(set(uploaded.rows) - set(rule_job_ids))
    only_rules = [j for j in rule_job_ids if j not in uploaded.rows]
    result.warnings += [Finding(Code.JOB_ONLY_IN_FILE, f"{j}는 규칙에 없는 JOB이라 판정에서 뺐습니다.", job_id=j)
                        for j in only_file]
    result.warnings += [Finding(Code.JOB_ONLY_IN_RULES, f"{j}는 파일에 없어 계산상 등록일이 모두 누락입니다.", job_id=j)
                        for j in only_rules]
    for job_id in rule_job_ids:
        expected = {d.day for d in schedule.dates_of(job_id)}
        marked = uploaded.rows.get(job_id, set())
        for day in range(1, uploaded.days + 1):
            if day in expected and day in marked:
                status = OK
            elif day in expected:
                status = MISSING
            elif day in marked:
                status = EXCESS
            else:
                status = OK
            result.verdicts.append(Verdict(job_id, first.replace(day=day), status))
    return result


# ================================================================ 화면·도구용 진입점


@dataclass
class UploadOutcome:
    message: str | None                 # "잘못된 파일입니다" 또는 None
    validation: ValidationResult
    judged: JudgeResult | None


def verify_upload(data_dir: Path, content: bytes, filename: str, confirmed) -> UploadOutcome:
    """업로드 파일 검증 전체 흐름. 파일 이름의 연월로 계산해 판정한다. 어떤 파일도 쓰지 않는다."""
    validation = validate_upload(content, filename)
    if not validation.is_valid:
        return UploadOutcome(INVALID_FILE_MESSAGE, validation, None)
    inputs = load_inputs(data_dir)
    schedule = compute_schedule(inputs, confirmed, validation.uploaded.year_month)
    judged = judge(validation.uploaded, schedule, [j.job_id for j in inputs.jobs])
    return UploadOutcome(None, validation, judged)
