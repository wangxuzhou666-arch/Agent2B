"""Upload guards: file type from the bytes, an xlsx archive check, text decoding.

All checks run before any parser touches the bytes. ``sniff_file_type`` decides the type from
the content, in this order: a zip archive is an xlsx; a legacy Office file (``.xls``) is
refused; other known binary signatures (PDF, images, gzip, executables, RAR, 7z, an empty zip)
are refused; a name ending in ``.xlsx`` over bytes that are no zip archive is refused; a NUL
byte in the first 8192 bytes is refused (binary, or UTF-16 text); any other text is read as
CSV. The file extension never says what a file *is*; the one thing it may do is contradict the
bytes, so a CSV saved as ``.xlsx`` is a file-type problem, not a CSV to parse.

An xlsx is a zip archive, and ``check_xlsx_archive`` does not take its word for what it holds.
From the central directory: the number of entries, the parts a workbook needs, no encryption,
only the stored and deflate methods (the two OOXML uses), and the declared sizes (their total
and the compression ratio). Then the real size of every entry, counted by inflating it in
chunks and stopping once it passes what the entry declared, because openpyxl inflates what is
there, not what the directory says.

The row and column limits bound the work of reading, not only what is kept. Every row the
reader pulls counts, empty ones included, and the read stops at ``UploadLimits.max_rows_read``:
twice ``max_rows`` plus the header, room for an empty row after every data row. Without that a
tiny sheet can put its one data row far down and keep the reader busy with the rows in between,
because the data rows alone never reach ``max_rows``. A row is read only as far as
``max_columns`` + 1 cells, a header with more than ``max_columns`` columns (empty cells at its
end are padding, not columns) is refused, and a data row is kept no wider than the header.

Every refusal is an ``UploadRejected`` that words itself in either reply language
(``UploadRejected.text``), so the API can answer in the language of the request.
"""

import copy
import io
import sys
import zipfile
import zlib
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..i18n import say

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0"  # legacy .xls / .doc
# first bytes -> the kind of file (a ``file_kind_<kind>`` message in core.i18n)
_BINARY_MAGIC = (
    (b"%PDF", "pdf"), (b"\x89PNG", "png"), (b"GIF8", "gif"), (b"\xff\xd8\xff", "jpeg"),
    (b"\x1f\x8b", "gzip"), (b"MZ", "executable"), (b"\x7fELF", "executable"),
    (b"Rar!", "rar"), (b"7z\xbc\xaf", "7z"), (b"PK\x05\x06", "empty_zip"),
)
_XLSX_REQUIRED = ("[Content_Types].xml", "xl/workbook.xml")
_XLSX_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)  # the two OOXML packages use
_UNREADABLE = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, ValueError)
_CHUNK = 64 * 1024
_ENCODINGS = ("utf-8-sig", "gb18030", "latin-1")  # utf-8-sig also reads BOM-less UTF-8


class UploadLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_bytes: int = 2 * 1024 * 1024
    max_rows: int = 2000  # data rows: non-empty rows below the header
    max_columns: int = 1024  # header cells, the empty ones at its end not counted
    max_uncompressed: int = 20 * 1024 * 1024  # declared total of all xlsx entries
    max_entries: int = 200
    max_ratio: int = 200  # per entry, applied to entries above 1 MiB uncompressed

    @property
    def max_rows_read(self) -> int:
        """Rows the reader pulls from the file, empty ones included: the header, and a data
        row followed by an empty one, ``max_rows`` times over."""
        return 2 * self.max_rows + 1


class UploadRejected(Exception):
    """A file-level problem. ``code`` is stable for API mapping; ``message`` is the English
    text; ``text(language)`` words the same problem in either reply language."""

    def __init__(self, code: str, message: str, *,
                 wording: Callable[[str | None], str] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self._wording = code, message, wording

    @classmethod
    def of(cls, code: str, key: str, **values: Any) -> "UploadRejected":
        """A rejection whose sentence is the ``core.i18n`` message ``key``, filled with
        ``values``."""
        return cls.worded(code, lambda language: say(key, language, **values))

    @classmethod
    def worded(cls, code: str, wording: Callable[[str | None], str]) -> "UploadRejected":
        """A rejection that words itself: ``wording(language)`` is its sentence, which lets a
        message name things (field labels, lists) in the reader's language."""
        return cls(code, wording("en"), wording=wording)

    def text(self, language: str | None = None) -> str:
        return self._wording(language) if self._wording else self.message


def sniff_file_type(raw: bytes, filename: str | None = None) -> str:
    """'xlsx' or 'csv' by content. Anything else raises UploadRejected('file_type').

    ``filename`` is only ever evidence against the bytes: a name that says ``.xlsx`` over
    content that is not a zip archive is refused rather than read as CSV (which would end in
    a confusing "required columns not found")."""
    if raw.startswith(ZIP_MAGIC):
        return "xlsx"
    if raw.startswith(OLE_MAGIC):
        raise UploadRejected.of("file_type", "upload_legacy_xls")
    for magic, kind in _BINARY_MAGIC:
        if raw.startswith(magic):
            raise UploadRejected.worded("file_type", lambda language, kind=kind: say(
                "upload_looks_like", language, kind=say(f"file_kind_{kind}", language)))
    if (filename or "").strip().casefold().endswith(".xlsx"):
        raise UploadRejected.of("file_type", "upload_xlsx_name")
    if b"\x00" in raw[:8192]:
        raise UploadRejected.of("file_type", "upload_binary_text")
    return "csv"


def _inflated_size(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, room: int) -> int:
    """Bytes ``entry`` really holds, counted by inflating it in chunks. Stops once the count
    passes ``room``, so "more than allowed" is known without inflating the rest.

    zipfile ends a member at its declared size, which is the archive's own claim; a copy of
    the entry with no size limit is read instead, so a member that holds more than it declares
    counts as more (and never ends at the claim with only a CRC error to show for it)."""
    unbounded = copy.copy(entry)
    unbounded.file_size = sys.maxsize
    size = 0
    with archive.open(unbounded) as stream:
        while size <= room and (chunk := stream.read(_CHUNK)):
            size += len(chunk)
    return size


def check_xlsx_archive(raw: bytes, limits: UploadLimits) -> None:
    """Zip-bomb check, before openpyxl opens the archive: what the central directory declares
    first (it costs nothing), then what every entry really holds."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, ValueError):
        raise UploadRejected.of("file_type", "upload_xlsx_unreadable") from None
    with archive:
        entries = archive.infolist()
        if len(entries) > limits.max_entries:
            raise UploadRejected.of("zip_bomb", "upload_xlsx_parts")
        names = {e.filename for e in entries}
        if not all(n in names for n in _XLSX_REQUIRED):
            raise UploadRejected.of("file_type", "upload_xlsx_not_workbook")
        if any(e.flag_bits & 0x1 for e in entries):
            raise UploadRejected.of("file_type", "upload_xlsx_encrypted")
        if any(e.compress_type not in _XLSX_METHODS for e in entries):
            raise UploadRejected.of("zip_bomb", "upload_xlsx_method")
        if sum(e.file_size for e in entries) > limits.max_uncompressed:
            raise UploadRejected.of("zip_bomb", "upload_xlsx_expands")
        for e in entries:
            ratio = e.file_size / e.compress_size if e.compress_size else float("inf")
            if e.file_size > 1024 * 1024 and ratio > limits.max_ratio:
                raise UploadRejected.of("zip_bomb", "upload_xlsx_ratio")
        spent = 0  # real bytes of the entries counted so far
        for entry in entries:
            room = min(entry.file_size, limits.max_uncompressed - spent)
            try:
                size = _inflated_size(archive, entry, room)
            except _UNREADABLE:
                raise UploadRejected.of("file_type", "upload_xlsx_unreadable") from None
            if size > room:
                raise UploadRejected.of("zip_bomb", "upload_xlsx_expands")
            spent += size


def check_upload(raw: bytes, limits: UploadLimits, filename: str | None = None) -> str:
    """Size and type checks; returns 'csv' or 'xlsx'. See ``sniff_file_type`` for ``filename``."""
    if not raw:
        raise UploadRejected.of("empty", "upload_empty")
    if len(raw) > limits.max_bytes:
        raise UploadRejected.of("too_large", "upload_too_large", limit=limits.max_bytes)
    kind = sniff_file_type(raw, filename)
    if kind == "xlsx":
        check_xlsx_archive(raw, limits)
    return kind


def decode_text(raw: bytes) -> tuple[str, str]:
    """Decode CSV bytes: UTF-8 (BOM optional), then GB18030, then Latin-1 (never fails)."""
    for encoding in _ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-8-sig":
            encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
        return text, encoding
    raise AssertionError("latin-1 decodes any byte string")  # pragma: no cover
