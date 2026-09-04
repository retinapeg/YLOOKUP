from __future__ import annotations

import io
import builtins
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.errors import WorkflowError, WorkflowErrorCode, WorkflowStage
from app.file_handling import (
    MAX_UPLOAD_BYTES,
    temporary_upload,
    validate_upload,
)


def _valid_pdf() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def _valid_xlsx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="Sheet1" sheetId="1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData/>
</worksheet>""",
        )
    return output.getvalue()


def _assert_error(
    filename: str,
    content: bytes,
    expected_code: WorkflowErrorCode,
    content_type: str | None = None,
) -> WorkflowError:
    with pytest.raises(WorkflowError) as caught:
        validate_upload(filename, content, content_type)
    assert caught.value.code is expected_code
    assert caught.value.stage is WorkflowStage.FILE_VALIDATION
    assert caught.value.retryable is False
    return caught.value


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "suffix"),
    [
        ("notice.txt", b"Fund: Example", "text/plain; charset=utf-8", ".txt"),
        ("notice.PDF", _valid_pdf(), "application/pdf", ".pdf"),
        (
            "records.xlsx",
            _valid_xlsx(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xlsx",
        ),
    ],
)
def test_validate_upload_accepts_supported_structures(
    filename, content, content_type, suffix
):
    result = validate_upload(filename, content, content_type)

    assert result.original_name == filename
    assert result.suffix == suffix
    assert result.size == len(content)
    assert result.data == content


def test_rejects_unsupported_suffix_and_mismatched_mime():
    _assert_error(
        "notice.docx", b"not empty", WorkflowErrorCode.UNSUPPORTED_FILE_TYPE
    )
    _assert_error(
        "notice.pdf",
        _valid_pdf(),
        WorkflowErrorCode.UNSUPPORTED_FILE_TYPE,
        "image/png",
    )


def test_rejects_empty_and_oversized_files_before_format_parsing():
    _assert_error("empty.txt", b"", WorkflowErrorCode.EMPTY_FILE)
    error = _assert_error(
        "large.txt",
        b"x" * (MAX_UPLOAD_BYTES + 1),
        WorkflowErrorCode.FILE_TOO_LARGE,
    )
    assert error.status_code == 413


@pytest.mark.parametrize(
    "content",
    [
        b"this is not a PDF",
        b"%PDF-1.7\ntruncated without a cross-reference\n%%EOF",
    ],
)
def test_rejects_spoofed_or_structurally_invalid_pdf(content):
    _assert_error("spoofed.pdf", content, WorkflowErrorCode.MALFORMED_PDF)


def test_rejects_spoofed_or_structurally_invalid_xlsx():
    _assert_error(
        "spoofed.xlsx", b"plain text", WorkflowErrorCode.MALFORMED_XLSX
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("unrelated.txt", "not a workbook")
    _assert_error(
        "incomplete.xlsx", output.getvalue(), WorkflowErrorCode.MALFORMED_XLSX
    )


@pytest.mark.parametrize("content", [b"\xff\xfe", b"valid prefix\x00binary suffix"])
def test_rejects_malformed_utf8_or_binary_text(content):
    _assert_error("notice.txt", content, WorkflowErrorCode.MALFORMED_TEXT)


def test_path_shaped_user_name_is_only_retained_as_safe_basename():
    result = validate_upload(
        r"..\private\customer-notice.txt",
        b"Fund: Example",
        "text/plain",
    )

    assert result.original_name == "customer-notice.txt"


def test_temporary_upload_uses_random_internal_name_and_cleans_up(tmp_path):
    original_name = "sensitive-client-name.txt"
    with temporary_upload(
        f"../../{original_name}",
        b"Fund: Example",
        "text/plain",
        temp_root=tmp_path,
    ) as staged_path:
        assert staged_path.exists()
        assert staged_path.read_bytes() == b"Fund: Example"
        assert original_name not in staged_path.name
        assert staged_path.suffix == ".txt"
        retained_path = staged_path

    assert not retained_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_temporary_upload_cleans_up_after_consumer_failure(tmp_path):
    retained_path: Path | None = None
    with pytest.raises(RuntimeError, match="consumer failed"):
        with temporary_upload(
            "notice.txt",
            b"Fund: Example",
            temp_root=tmp_path,
        ) as staged_path:
            retained_path = staged_path
            raise RuntimeError("consumer failed")

    assert retained_path is not None
    assert not retained_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_validated_upload_repr_does_not_include_document_contents():
    marker = b"do-not-log-this-document-body"
    result = validate_upload("notice.txt", marker)

    assert marker.decode() not in repr(result)


def test_pdf_can_be_staged_when_optional_parser_is_unavailable(monkeypatch):
    real_import = builtins.__import__

    def import_without_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("optional parser unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_pypdf)

    result = validate_upload("notice.pdf", _valid_pdf(), "application/pdf")

    assert result.suffix == ".pdf"
