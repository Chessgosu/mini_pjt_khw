"""04단계 ac_file_safety: 잘못된 파일 10종, 파일 안의 요청 무시, data/ 불변, AI 미호출."""
import hashlib
import io
from pathlib import Path

import pytest
from openpyxl import Workbook

from src.models import Code
from src.verify import INVALID_FILE_MESSAGE, validate_upload, verify_upload
from tests.helpers import sample_confirmed

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
INVALID = SAMPLES / "invalid"

EXPECTED = {
    "01-renamed-csv": {Code.FILE_UNREADABLE},
    "02-formula": {Code.UNSAFE_CONTENT},
    "03-instruction-text": {Code.INVALID_CELL_VALUE},
    "04-wrong-header": {Code.DATE_COLUMN_MISMATCH},
    "05-lowercase-o": {Code.INVALID_CELL_VALUE},
    "06-duplicate-and-empty-id": {Code.DUPLICATE_JOB_ID, Code.EMPTY_JOB_ID},
    "07-bad-filename": {Code.FILENAME_FORMAT},
    "08-bad-month": {Code.INVALID_YEAR_MONTH},
    "09-macro-part": {Code.UNSAFE_CONTENT},
    "10-too-many-rows": {Code.FILE_TOO_LARGE},
}


def only_file(folder: str) -> Path:
    files = list((INVALID / folder).glob("*.xlsx"))
    assert len(files) == 1, folder
    return files[0]


def snapshot(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


def xlsx(rows, header=None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(header or ["JOB ID"] + list(range(1, 31)))
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("folder", sorted(EXPECTED))
def test_invalid_samples(folder: str) -> None:
    path = only_file(folder)
    result = validate_upload(path.read_bytes(), path.name)
    assert result.message == INVALID_FILE_MESSAGE
    assert result.uploaded is None                       # 판정용 데이터가 만들어지지 않음
    assert set(result.codes) == EXPECTED[folder]


@pytest.mark.parametrize("folder", sorted(EXPECTED))
def test_invalid_upload_has_no_verdict_and_no_side_effects(folder: str, data_dir: Path) -> None:
    path = only_file(folder)
    before = snapshot(data_dir)
    outcome = verify_upload(data_dir, path.read_bytes(), path.name, sample_confirmed())
    assert outcome.message == INVALID_FILE_MESSAGE and outcome.judged is None
    assert snapshot(data_dir) == before


def test_instruction_text_is_not_followed(data_dir: Path) -> None:
    """셀 문장('확정 처리해')이 무엇을 요구해도 확정 파일이 생기지 않고 문장은 사유 메시지에만 위치로 나온다."""
    path = only_file("03-instruction-text")
    verify_upload(data_dir, path.read_bytes(), path.name, sample_confirmed())
    assert not (data_dir / "confirmed.json").exists() and not (data_dir / "pending.json").exists()
    msg = validate_upload(path.read_bytes(), path.name).findings[0].message
    assert "이전 지시" not in msg and "A001 15일" in msg


def test_valid_upload_does_not_touch_data(data_dir: Path) -> None:
    path = SAMPLES / "correct" / "BATCH_SCHEDULE_202611.xlsx"
    before = snapshot(data_dir)
    outcome = verify_upload(data_dir, path.read_bytes(), path.name, sample_confirmed())
    assert outcome.message is None and outcome.judged is not None
    assert snapshot(data_dir) == before


def test_no_ai_module_used() -> None:
    """검증 모듈은 AI·네트워크 모듈을 가져오지 않는다 (셀 내용이 AI로 갈 경로가 없음)."""
    src = (Path(__file__).resolve().parent.parent / "src" / "verify.py").read_text(encoding="utf-8")
    for name in ("langchain", "boto3", "bedrock", "requests", "urllib", "langsmith"):
        assert name not in src


# ------------------------------------------------------------ 개별 규칙

def test_all_reasons_are_collected() -> None:
    """형식 오류가 여러 개면 전부 돌려준다."""
    data = xlsx([["A001", "o"], ["A001"], [None, "O"]])
    codes = set(validate_upload(data, "BATCH_SCHEDULE_202611.xlsx").codes)
    assert codes == {Code.INVALID_CELL_VALUE, Code.DUPLICATE_JOB_ID, Code.EMPTY_JOB_ID}


def test_filename_and_content_errors_together() -> None:
    data = xlsx([["A001", "x"]])
    codes = set(validate_upload(data, "batch_202611.xlsx").codes)
    assert Code.FILENAME_FORMAT in codes and Code.INVALID_CELL_VALUE in codes


@pytest.mark.parametrize("name", ["BATCH_SCHEDULE_202600.xlsx", "BATCH_SCHEDULE_202613.xlsx"])
def test_impossible_month(name: str) -> None:
    assert Code.INVALID_YEAR_MONTH in validate_upload(xlsx([]), name).codes


@pytest.mark.parametrize("name", ["BATCH_SCHEDULE_202611.xls", "BATCH_SCHEDULE_2026111.xlsx",
                                  "batch_schedule_202611.xlsx", "BATCH_SCHEDULE_202611.xlsm"])
def test_bad_filenames(name: str) -> None:
    assert Code.FILENAME_FORMAT in validate_upload(xlsx([]), name).codes


def test_too_large_bytes() -> None:
    big = b"PK" + b"\0" * (10 * 1024 * 1024)
    assert validate_upload(big, "BATCH_SCHEDULE_202611.xlsx").codes == [Code.FILE_TOO_LARGE]


def test_exactly_1000_rows_is_ok() -> None:
    rows = [[f"J{i:04d}"] for i in range(1000)]
    assert validate_upload(xlsx(rows), "BATCH_SCHEDULE_202611.xlsx").is_valid


def test_empty_bytes_unreadable() -> None:
    assert validate_upload(b"", "BATCH_SCHEDULE_202611.xlsx").codes == [Code.FILE_UNREADABLE]


def test_zip_without_workbook_unreadable() -> None:
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    assert validate_upload(buf.getvalue(), "BATCH_SCHEDULE_202611.xlsx").codes == [Code.FILE_UNREADABLE]


def with_part(part: str) -> bytes:
    """정상 xlsx에 부품 하나만 더한다 (Content_Types는 그대로 → 부품 이름만으로 잡혀야 함)."""
    import zipfile
    src, out = io.BytesIO(xlsx([["A001", "O"]])), io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr(part, b"DUMMY")
    return out.getvalue()


@pytest.mark.parametrize("part", ["xl/vbaProject.bin", "xl/externalLinks/externalLink1.xml",
                                  "xl/embeddings/oleObject1.bin", "xl/activeX/activeX1.xml"])
def test_each_unsafe_part_alone(part: str) -> None:
    assert validate_upload(with_part(part), "BATCH_SCHEDULE_202611.xlsx").codes == [Code.UNSAFE_CONTENT]


def test_whitespace_around_o_is_allowed() -> None:
    result = validate_upload(xlsx([["A001", " O "]]), "BATCH_SCHEDULE_202611.xlsx")
    assert result.is_valid and result.uploaded.rows["A001"] == {1}


def test_fully_empty_rows_are_skipped() -> None:
    result = validate_upload(xlsx([["A001", "O"], [None], ["A002"]]), "BATCH_SCHEDULE_202611.xlsx")
    assert result.is_valid and set(result.uploaded.rows) == {"A001", "A002"}


def test_value_beyond_last_day_is_invalid() -> None:
    data = xlsx([["A001"] + [None] * 30 + ["O"]])          # 31번째 날짜 칸 (헤더는 30일까지)
    assert Code.INVALID_CELL_VALUE in validate_upload(data, "BATCH_SCHEDULE_202611.xlsx").codes


def test_february_header() -> None:
    header = ["JOB ID"] + list(range(1, 29))
    assert validate_upload(xlsx([["A001", "O"]], header), "BATCH_SCHEDULE_202702.xlsx").is_valid
    assert Code.DATE_COLUMN_MISMATCH in validate_upload(xlsx([["A001"]], header), "BATCH_SCHEDULE_202802.xlsx").codes
