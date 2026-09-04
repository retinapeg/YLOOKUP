"""Small, defensive helpers for validating and staging user uploads.

Validation happens before a file is written to disk.  Staged files receive a
random internal name, live in a private temporary directory, and are removed
when the context manager exits (including exceptional exits).
"""

from __future__ import annotations

import io
import re
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Mapping, Optional, Union
from uuid import uuid4
from xml.etree import ElementTree

from app.errors import WorkflowError, WorkflowErrorCode, WorkflowStage


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
"""Maximum accepted compressed/uploaded file size (10 MiB)."""

_MAX_XLSX_MEMBERS = 5_000
_MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
_ALLOWED_MIME_TYPES: Mapping[str, frozenset[str]] = {
    ".pdf": frozenset(
        {
            "application/pdf",
            "application/x-pdf",
            "application/octet-stream",
        }
    ),
    ".txt": frozenset({"text/plain", "application/octet-stream"}),
    ".xlsx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/zip",
            "application/x-zip-compressed",
            "application/octet-stream",
        }
    ),
}
ALLOWED_SUFFIXES = frozenset(_ALLOWED_MIME_TYPES)


@dataclass(frozen=True)
class ValidatedUpload:
    """Validated upload metadata and bytes.

    ``data`` is excluded from ``repr`` so an accidental object log does not
    disclose source-document contents.
    """

    original_name: str
    suffix: str
    size: int
    content_type: Optional[str]
    data: bytes = field(repr=False)


def _public_error(
    code: WorkflowErrorCode,
    request_id: str,
) -> WorkflowError:
    return WorkflowError(
        code,
        request_id=request_id,
        stage=WorkflowStage.FILE_VALIDATION,
    )


def _safe_basename(filename: object) -> str:
    """Return display-only basename for POSIX- or Windows-shaped input paths."""

    if not isinstance(filename, str):
        return ""
    basename = re.split(r"[/\\\\]", filename)[-1]
    basename = "".join(character for character in basename if character.isprintable())
    basename = basename.strip()
    return "" if basename in {"", ".", ".."} else basename


def _normalize_content_type(content_type: Optional[str]) -> Optional[str]:
    if content_type is None or not str(content_type).strip():
        return None
    return str(content_type).split(";", 1)[0].strip().casefold()


def _validate_pdf(data: bytes, *, request_id: str) -> None:
    header = data[:1_024]
    header_position = header.find(b"%PDF-")
    if header_position < 0 or not re.match(rb"%PDF-\d\.\d", header[header_position:]):
        raise _public_error(
            WorkflowErrorCode.MALFORMED_PDF,
            request_id,
        )
    if b"%%EOF" not in data[-4_096:]:
        raise _public_error(
            WorkflowErrorCode.MALFORMED_PDF,
            request_id,
        )

    try:
        from pypdf import PdfReader
    except ImportError:
        # Structural checks above are sufficient to stage the file. The
        # extraction layer will disclose parser unavailability and degrade to
        # a bundled sidecar (demo) or an empty reviewable result (upload).
        return

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("password-protected PDF")
        if len(reader.pages) < 1:
            raise ValueError("PDF has no pages")
    except Exception as exc:
        raise _public_error(
            WorkflowErrorCode.MALFORMED_PDF,
            request_id,
        ) from exc


def _xml_root(archive: zipfile.ZipFile, member: str) -> ElementTree.Element:
    return ElementTree.fromstring(archive.read(member))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _validate_xlsx(data: bytes, *, request_id: str) -> None:
    if not data.startswith(b"PK\x03\x04"):
        raise _public_error(
            WorkflowErrorCode.MALFORMED_XLSX,
            request_id,
        )

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            name_set = set(names)

            if not entries or len(entries) > _MAX_XLSX_MEMBERS:
                raise ValueError("unexpected XLSX member count")
            if len(names) != len(name_set):
                raise ValueError("duplicate XLSX members")
            if any(
                name.startswith("/") or ".." in Path(name).parts for name in names
            ):
                raise ValueError("unsafe XLSX member path")
            if any(entry.flag_bits & 0x1 for entry in entries):
                raise ValueError("encrypted XLSX member")
            if sum(entry.file_size for entry in entries) > _MAX_XLSX_UNCOMPRESSED_BYTES:
                raise ValueError("XLSX expands beyond the processing limit")
            if archive.testzip() is not None:
                raise ValueError("XLSX checksum failure")

            required = {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"}
            if not required.issubset(name_set):
                raise ValueError("missing XLSX package members")
            worksheet_names = sorted(
                name
                for name in name_set
                if name.startswith("xl/worksheets/") and name.endswith(".xml")
            )
            if not worksheet_names:
                raise ValueError("XLSX has no worksheets")

            content_types = _xml_root(archive, "[Content_Types].xml")
            relationships = _xml_root(archive, "_rels/.rels")
            workbook = _xml_root(archive, "xl/workbook.xml")
            worksheet = _xml_root(archive, worksheet_names[0])
            if _local_name(content_types.tag) != "Types":
                raise ValueError("invalid XLSX content types")
            if _local_name(relationships.tag) != "Relationships":
                raise ValueError("invalid XLSX package relationships")
            if _local_name(workbook.tag) != "workbook":
                raise ValueError("invalid XLSX workbook XML")
            if _local_name(worksheet.tag) != "worksheet":
                raise ValueError("invalid XLSX worksheet XML")

            workbook_declarations = [
                element
                for element in content_types
                if element.attrib.get("PartName") == "/xl/workbook.xml"
            ]
            if not workbook_declarations or not any(
                element.attrib.get("ContentType")
                == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
                for element in workbook_declarations
            ):
                raise ValueError("XLSX workbook content type is invalid")
    except WorkflowError:
        raise
    except (
        ElementTree.ParseError,
        KeyError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        raise _public_error(
            WorkflowErrorCode.MALFORMED_XLSX,
            request_id,
        ) from exc


def _validate_text(data: bytes, *, request_id: str) -> None:
    try:
        decoded = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise _public_error(
            WorkflowErrorCode.MALFORMED_TEXT,
            request_id,
        ) from exc
    if "\x00" in decoded:
        raise _public_error(
            WorkflowErrorCode.MALFORMED_TEXT,
            request_id,
        )


def validate_upload(
    filename: object,
    content: Union[bytes, bytearray, memoryview],
    content_type: Optional[str] = None,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    request_id: Optional[str] = None,
) -> ValidatedUpload:
    """Validate upload metadata and structure, returning normalized metadata.

    The file extension and MIME type are useful gates, but neither is trusted as
    proof of format.  Each supported type also receives a lightweight structural
    validation before it can be staged or parsed by the workflow.
    """

    correlation_id = request_id or str(uuid4())
    original_name = _safe_basename(filename)
    suffix = Path(original_name).suffix.casefold() if original_name else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise _public_error(
            WorkflowErrorCode.UNSUPPORTED_FILE_TYPE,
            correlation_id,
        )
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise TypeError("content must be bytes-like")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")

    size = content.nbytes if isinstance(content, memoryview) else len(content)
    if size == 0:
        raise _public_error(
            WorkflowErrorCode.EMPTY_FILE,
            correlation_id,
        )
    if size > max_bytes:
        raise _public_error(
            WorkflowErrorCode.FILE_TOO_LARGE,
            correlation_id,
        )

    normalized_content_type = _normalize_content_type(content_type)
    if (
        normalized_content_type is not None
        and normalized_content_type not in _ALLOWED_MIME_TYPES[suffix]
    ):
        raise _public_error(
            WorkflowErrorCode.UNSUPPORTED_FILE_TYPE,
            correlation_id,
        )

    data = bytes(content)
    if suffix == ".pdf":
        _validate_pdf(data, request_id=correlation_id)
    elif suffix == ".xlsx":
        _validate_xlsx(data, request_id=correlation_id)
    else:
        _validate_text(data, request_id=correlation_id)

    return ValidatedUpload(
        original_name=original_name,
        suffix=suffix,
        size=size,
        content_type=normalized_content_type,
        data=data,
    )


@contextmanager
def temporary_upload(
    filename: object,
    content: Union[bytes, bytearray, memoryview],
    content_type: Optional[str] = None,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    temp_root: Optional[Union[str, Path]] = None,
    request_id: Optional[str] = None,
) -> Iterator[Path]:
    """Validate and stage an upload under a random name for the context lifetime."""

    validated = validate_upload(
        filename,
        content,
        content_type,
        max_bytes=max_bytes,
        request_id=request_id,
    )
    root = str(Path(temp_root)) if temp_root is not None else None
    with tempfile.TemporaryDirectory(prefix="fundops-upload-", dir=root) as directory:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="upload-",
            suffix=validated.suffix,
            dir=directory,
            delete=False,
        ) as staged:
            staged.write(validated.data)
            staged_path = Path(staged.name)
        yield staged_path


__all__ = [
    "ALLOWED_SUFFIXES",
    "MAX_UPLOAD_BYTES",
    "ValidatedUpload",
    "temporary_upload",
    "validate_upload",
]
