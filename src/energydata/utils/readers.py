"""Turn JSON, XML, CSV and ZIP response bodies into plain Python data."""

import csv
import io
import json
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from typing import Literal

import httpx

type Format = Literal["json", "xml", "csv", "zip"]

FORMATS: tuple[Format, ...] = ("json", "xml", "csv", "zip")

type JsonValue = (
    dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None
)

type Parsed = JsonValue | ET.Element | list[dict[str, str]] | dict[str, Parsed] | bytes

_CSV_DELIMITERS = ",;\t"
_SNIFF_BYTES = 4096
_ZIP_ERRORS = (
    zipfile.BadZipFile,
    RuntimeError,
    NotImplementedError,
    zlib.error,
    EOFError,
    OSError,
    ValueError,
    struct.error,
)


class ParseError(ValueError):
    """A body could not be parsed.

    Attributes:
        format: The format being parsed, or `None` when it could not be
            determined.
        detail: The message without the format prefix.
    """

    def __init__(self, fmt: Format | None, message: str) -> None:
        """Build the error; the message begins with the format name.

        Args:
            fmt: The format being parsed, or `None` when unknown.
            message: What went wrong.
        """
        super().__init__(f"{fmt}: {message}" if fmt is not None else message)
        self.format = fmt
        self.detail = message


def read_json(data: bytes) -> JsonValue:
    """Parse a JSON document.

    Args:
        data: The document, in any encoding `json.loads` detects.

    Returns:
        The decoded value.

    Raises:
        ParseError: If the data is empty or malformed.
    """
    try:
        value: JsonValue = json.loads(data)
    except (ValueError, RecursionError) as error:
        raise ParseError("json", str(error) or "invalid document") from error
    return value


def read_xml(data: bytes) -> ET.Element:
    """Parse an XML document.

    Args:
        data: The document.

    Returns:
        The root element.

    Raises:
        ParseError: If the data is empty or malformed, or declares an encoding
            Python does not know.
    """
    try:
        return ET.fromstring(data)
    except (ET.ParseError, LookupError) as error:
        raise ParseError("xml", str(error)) from error


def read_csv(
    data: bytes, *, delimiter: str | None = None, encoding: str = "utf-8-sig"
) -> list[dict[str, str]]:
    """Parse CSV into one dict per row, keyed by the header row.

    Args:
        data: The file contents.
        delimiter: The field separator. `None` sniffs among `,`, `;` and tab and
            falls back to `,` when sniffing fails, as for a one-column file.
        encoding: The text encoding of `data`. The default drops a leading BOM.

    Returns:
        The rows in order. Empty for empty input or a header with no rows. Blank
        lines are skipped.

    Raises:
        ParseError: If the encoding is unknown, the bytes do not decode, the
            delimiter is not one character, a header name repeats, a quoted field
            is not closed, or a row has more or fewer fields than the header.
    """
    try:
        text = data.decode(encoding)
    except LookupError as error:
        raise ParseError("csv", f"unknown encoding {encoding!r}") from error
    except UnicodeDecodeError as error:
        raise ParseError("csv", f"cannot decode as {encoding}: {error}") from error
    separator = delimiter if delimiter is not None else _sniff_delimiter(text)
    if len(separator) != 1:
        raise ParseError("csv", f"delimiter must be one character, got {separator!r}")
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    try:
        reader = csv.reader(
            io.StringIO(text, newline=""), delimiter=separator, strict=True
        )
        for number, fields in enumerate(reader, start=1):
            if not fields:
                continue
            if header is None:
                header = fields
                for name in header:
                    if header.count(name) > 1:
                        raise ParseError("csv", f"duplicate header name {name!r}")
                continue
            if len(fields) != len(header):
                raise ParseError(
                    "csv",
                    f"row {number} has {len(fields)} fields, header has {len(header)}",
                )
            rows.append(dict(zip(header, fields, strict=True)))
    except csv.Error as error:
        raise ParseError("csv", str(error)) from error
    return rows


def _sniff_delimiter(text: str) -> str:
    """Guess the delimiter of CSV text, defaulting to a comma."""
    try:
        return (
            csv.Sniffer()
            .sniff(text[:_SNIFF_BYTES], delimiters=_CSV_DELIMITERS)
            .delimiter
        )
    except csv.Error:
        return ","


def read_zip(data: bytes) -> dict[str, Parsed]:
    """Read a ZIP archive, parsing each member by its file extension.

    Args:
        data: The archive.

    Returns:
        `{member name: parsed content}` in archive order. Directory entries are
        skipped. A member with an unknown extension is returned as raw bytes; a
        nested ZIP is read recursively.

    Raises:
        ParseError: If `data` is not a ZIP archive, or a member is unreadable
            or malformed (the message names the member).
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except _ZIP_ERRORS as error:
        raise ParseError("zip", str(error)) from error
    members: dict[str, Parsed] = {}
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            fmt = format_from_filename(name)
            try:
                content = archive.read(info)
            except _ZIP_ERRORS as error:
                raise ParseError("zip", f"member {name!r}: {error}") from error
            if fmt is None:
                members[name] = content
                continue
            try:
                members[name] = read_bytes(content, fmt)
            except ParseError as error:
                raise ParseError(
                    error.format, f"member {name!r}: {error.detail}"
                ) from error
    return members


def format_from_content_type(content_type: str) -> Format | None:
    """Map a `Content-Type` header value to a format.

    Args:
        content_type: The header value. Case and parameters are ignored.

    Returns:
        The format, or `None` for any other media type.
    """
    media = content_type.split(";", 1)[0].strip().lower()
    if media in ("application/json", "text/json") or (
        "/" in media and media.endswith("+json")
    ):
        return "json"
    if media in ("application/xml", "text/xml") or (
        "/" in media and media.endswith("+xml")
    ):
        return "xml"
    if media in ("text/csv", "application/csv"):
        return "csv"
    if media in (
        "application/zip",
        "application/x-zip-compressed",
        "application/x-zip",
    ):
        return "zip"
    return None


def format_from_filename(name: str) -> Format | None:
    """Map a file name to a format by its extension.

    Args:
        name: The file name or path. Case is ignored.

    Returns:
        The format, or `None` for an unknown extension.
    """
    lowered = name.lower()
    for fmt in FORMATS:
        if lowered.endswith(f".{fmt}"):
            return fmt
    return None


def read_bytes(data: bytes, fmt: Format) -> Parsed:
    """Parse `data` as the named format.

    Args:
        data: The body.
        fmt: One of `FORMATS`.

    Returns:
        What the reader for `fmt` returns.

    Raises:
        ValueError: If `fmt` is not one of `FORMATS`.
        ParseError: If the data is malformed.
    """
    if fmt == "json":
        return read_json(data)
    if fmt == "xml":
        return read_xml(data)
    if fmt == "csv":
        return read_csv(data)
    if fmt == "zip":
        return read_zip(data)
    raise ValueError(f"fmt must be one of {', '.join(FORMATS)}, got {fmt!r}")


def read_response(response: httpx.Response, fmt: Format | None = None) -> Parsed:
    """Parse a response body.

    Args:
        response: The response, with its body read.
        fmt: The format to use. `None` picks it from the `Content-Type` header.

    Returns:
        The parsed body. For CSV, a `charset` in `Content-Type` is the encoding.

    Raises:
        ParseError: If the format cannot be told from the header, or the body is
            malformed.
        ValueError: If `fmt` is not one of `FORMATS`.
    """
    if fmt is None:
        content_type = response.headers.get("Content-Type", "")
        fmt = format_from_content_type(content_type)
        if fmt is None:
            raise ParseError(
                None, f"cannot tell the format from Content-Type {content_type!r}"
            )
    charset = response.charset_encoding
    if fmt == "csv" and charset is not None:
        if charset.lower().replace("_", "-") in ("utf-8", "utf8"):
            charset = "utf-8-sig"  # as for read_csv's default: a BOM is not data
        return read_csv(response.content, encoding=charset)
    return read_bytes(response.content, fmt)


def main() -> None:
    """Showcase this module's functionality."""
    json_body = b'{"records": [{"price": 41.5}, {"price": 38.0}]}'
    csv_body = b"area;price\nDK1;41,5\nDK2;38,0\n"
    xml_body = b"<doc><period start='2026-01-01'/></doc>"

    parsed_json = read_json(json_body)
    parsed_csv = read_csv(csv_body)  # the ";" delimiter is sniffed

    print(f"json: {parsed_json!r}")
    print(f"csv: {parsed_csv}")

    # A ZIP holding an XML, a CSV and a member of unknown type.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("series.xml", xml_body)
        archive.writestr("prices.csv", csv_body)
        archive.writestr("notes.txt", b"raw")
    archive_body = buffer.getvalue()

    members = read_zip(archive_body)

    print(f"zip members: {list(members)}")
    print(f"zip prices.csv: {members['prices.csv']!r}")
    print(f"zip notes.txt: {members['notes.txt']!r}")

    # A response is read by its Content-Type, or by an explicit format.
    response = httpx.Response(
        200,
        headers={"Content-Type": "application/json; charset=utf-8"},
        content=json_body,
    )
    fmt = None  # None, "json", "xml", "csv", "zip"

    body = read_response(response, fmt)

    print(f"response read by Content-Type: {body!r}")


if __name__ == "__main__":
    main()
