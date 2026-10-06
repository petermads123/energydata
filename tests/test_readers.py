import contextlib
import io
import math
import re
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import httpx
import pytest

from energydata.utils import (
    FORMATS,
    ParseError,
    read_bytes,
    read_csv,
    read_json,
    read_response,
    read_xml,
    read_zip,
)
from energydata.utils.readers import format_from_content_type, format_from_filename


def _zip(*members: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members:
            archive.writestr(name, content)
    return buffer.getvalue()


def _response(content_type: str | None, body: bytes) -> httpx.Response:
    headers = {} if content_type is None else {"Content-Type": content_type}
    return httpx.Response(200, headers=headers, content=body)


# --- read_json ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b'{"a": [1, 2.5, null, true]}', {"a": [1, 2.5, None, True]}),
        (b"null", None),
        (b"0", 0),
        (b'"x"', "x"),
        (b"[]", []),
        (b"{}", {}),
        ('{"navn": "Køge æøå"}'.encode(), {"navn": "Køge æøå"}),
        (b'{"a": 1}\n', {"a": 1}),
        (b"\xef\xbb\xbf{}", {}),  # a BOM is tolerated by json.loads on bytes
        ('{"a": "æ"}'.encode("utf-16"), {"a": "æ"}),
    ],
)
def test_read_json_parses_valid_documents(data: bytes, expected: object) -> None:
    assert read_json(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"   ",
        b"{",
        b'{"a":',
        b'{"a": 1,}',
        b"\xff",
        b'"\xff"',
        b"[" * 100_000 + b"]" * 100_000,  # recursion limit
        b"1" * 5000,  # integer digit limit
    ],
)
def test_read_json_raises_a_parse_error_naming_json(data: bytes) -> None:
    with pytest.raises(ParseError) as info:
        read_json(data)

    assert info.value.format == "json"
    assert str(info.value).startswith("json: ")
    assert isinstance(info.value, ValueError)


def test_read_json_chains_the_underlying_error() -> None:
    with pytest.raises(ParseError) as info:
        read_json(b"{")

    assert isinstance(info.value.__cause__, ValueError)


def test_read_json_accepts_nan_as_a_float() -> None:
    # Documented, not endorsed: the standard library's lenient extension passes through.
    value = read_json(b"NaN")

    assert isinstance(value, float)
    assert math.isnan(value)


# --- read_xml ----------------------------------------------------------------


def test_read_xml_returns_the_root_element() -> None:
    root = read_xml(b"<doc><a x='1'>text</a></doc>")

    assert root.tag == "doc"
    child = root.find("a")
    assert child is not None
    assert child.attrib == {"x": "1"}
    assert child.text == "text"


def test_read_xml_honours_a_declared_encoding() -> None:
    root = read_xml(b'<?xml version="1.0" encoding="ISO-8859-1"?><a>\xe6</a>')

    assert root.text == "æ"


def test_read_xml_accepts_a_bom() -> None:
    assert read_xml(b"\xef\xbb\xbf<a>x</a>").text == "x"


def test_read_xml_keeps_non_ascii_text() -> None:
    assert read_xml("<a>Køge æøå</a>".encode()).text == "Køge æøå"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"   ",
        b"<a>",
        b"<a></b>",
        b"<a/><b/>",  # two roots
        b"not xml",
        b'\n<?xml version="1.0"?><a/>',  # declaration not at the start
    ],
)
def test_read_xml_raises_a_parse_error_naming_xml(data: bytes) -> None:
    with pytest.raises(ParseError) as info:
        read_xml(data)

    assert info.value.format == "xml"
    assert str(info.value).startswith("xml: ")


def test_read_xml_unknown_declared_encoding_is_a_parse_error() -> None:
    # pyexpat surfaces this as a LookupError, which would escape a bare ET.ParseError.
    with pytest.raises(ParseError) as info:
        read_xml(b'<?xml version="1.0" encoding="no-such-codec"?><a/>')

    assert info.value.format == "xml"
    assert "no-such-codec" in str(info.value)


# --- read_csv ----------------------------------------------------------------


def test_read_csv_returns_rows_keyed_by_the_header() -> None:
    assert read_csv(b"area,price\nDK1,41.5\nDK2,38\n") == [
        {"area": "DK1", "price": "41.5"},
        {"area": "DK2", "price": "38"},
    ]


@pytest.mark.parametrize("separator", [",", ";", "\t"])
def test_read_csv_sniffs_each_supported_delimiter(separator: str) -> None:
    data = f"a{separator}b\n1{separator}2\n3{separator}4\n".encode()

    assert read_csv(data) == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_read_csv_sniffs_a_semicolon_file_with_decimal_commas() -> None:
    data = b"HourDK;SpotPriceDKK\n2024-01-01T00:00;123,45\n2024-01-01T01:00;120,10\n"

    rows = read_csv(data)

    assert [row["SpotPriceDKK"] for row in rows] == ["123,45", "120,10"]


def test_read_csv_explicit_delimiter_beats_sniffing() -> None:
    data = b"price\n41,5\n38,0\n" + b"40,0\n" * 30

    rows = read_csv(data, delimiter=";")

    assert rows[0] == {"price": "41,5"}
    assert len(rows) == 32


def test_read_csv_one_column_file_falls_back_to_comma() -> None:
    assert read_csv(b"price\n1\n2\n") == [{"price": "1"}, {"price": "2"}]


def test_read_csv_one_column_with_decimal_commas_is_ragged_unless_told() -> None:
    # Documents a trap: the sniffer picks "," here, so the rows look ragged.
    data = b"price\n41,5\n38,0\n" + b"40,0\n" * 30

    with pytest.raises(ParseError, match="row 2"):
        read_csv(data)


def test_read_csv_drops_a_leading_bom_by_default() -> None:
    assert read_csv(b"\xef\xbb\xbfa,b\n1,2\n") == [{"a": "1", "b": "2"}]


def test_read_csv_decodes_the_requested_encoding() -> None:
    data = "navn\nKøge\n".encode("latin-1")

    assert read_csv(data, encoding="latin-1") == [{"navn": "Køge"}]


def test_read_csv_keeps_non_ascii_values() -> None:
    assert read_csv("navn,by\nÆbleø,Århus\n".encode()) == [
        {"navn": "Æbleø", "by": "Århus"}
    ]


@pytest.mark.parametrize(
    "data", [b"", b"\n\n", b"a,b", b"a,b\n", b"a,b\r\n", b"\xef\xbb\xbf"]
)
def test_read_csv_returns_no_rows_for_empty_or_header_only_input(data: bytes) -> None:
    assert read_csv(data) == []


def test_read_csv_skips_blank_lines() -> None:
    assert read_csv(b"a,b\n\n1,2\n\n3,4\n") == [
        {"a": "1", "b": "2"},
        {"a": "3", "b": "4"},
    ]


def test_read_csv_handles_crlf_line_endings() -> None:
    assert read_csv(b"a,b\r\n1,2\r\n") == [{"a": "1", "b": "2"}]


def test_read_csv_keeps_quoted_delimiters_and_newlines() -> None:
    assert read_csv(b'a,b\n"x\ny","1,2"\n') == [{"a": "x\ny", "b": "1,2"}]


def test_read_csv_preserves_surrounding_whitespace() -> None:
    assert read_csv(b"a, b\n1, 2\n") == [{"a": "1", " b": " 2"}]


def test_read_csv_allows_an_empty_header_name_once() -> None:
    assert read_csv(b"a,b,\n1,2,\n") == [{"a": "1", "b": "2", "": ""}]


@pytest.mark.parametrize(
    ("data", "row"),
    [
        (b"a,b\n1\n", "row 2"),
        (b"a,b\n1,2,3\n", "row 2"),
        (b"a,b\n1,2\n3\n", "row 3"),
        (b"a,b\n\n1\n", "row 3"),  # the record number counts blank lines
    ],
)
def test_read_csv_ragged_rows_raise_naming_the_row(data: bytes, row: str) -> None:
    with pytest.raises(ParseError, match=row) as info:
        read_csv(data)

    assert info.value.format == "csv"


def test_read_csv_trailing_delimiter_makes_a_ragged_row() -> None:
    with pytest.raises(ParseError, match="row 2 has 3 fields, header has 2"):
        read_csv(b"a;b\n1;2;\n", delimiter=";")


def test_read_csv_sniffs_a_header_plus_one_row() -> None:
    assert read_csv(b"a;b\n1;2\n") == [{"a": "1", "b": "2"}]


@pytest.mark.parametrize(
    ("data", "name"), [(b"a,a\n1,2\n", "'a'"), (b",\n1,2\n", "''")]
)
def test_read_csv_duplicate_header_names_raise_naming_the_name(
    data: bytes, name: str
) -> None:
    with pytest.raises(ParseError, match="duplicate header") as info:
        read_csv(data)

    assert name in str(info.value)


def test_read_csv_truncated_quoted_field_is_a_parse_error() -> None:
    with pytest.raises(ParseError) as info:
        read_csv(b'a,b\n1,"2')

    assert info.value.format == "csv"


def test_read_csv_undecodable_bytes_are_a_parse_error() -> None:
    with pytest.raises(ParseError, match="cannot decode") as info:
        read_csv(b"a\n\xff\n")

    assert info.value.format == "csv"


def test_read_csv_unknown_encoding_is_a_parse_error_naming_it() -> None:
    with pytest.raises(ParseError, match="nope") as info:
        read_csv(b"a\n1\n", encoding="nope")

    assert info.value.format == "csv"


@pytest.mark.parametrize("delimiter", ["", ";;", "ab"])
def test_read_csv_invalid_delimiter_is_a_parse_error_naming_it(delimiter: str) -> None:
    with pytest.raises(ParseError, match="delimiter") as info:
        read_csv(b"a\n1\n", delimiter=delimiter)

    assert repr(delimiter) in str(info.value)


def test_read_csv_field_over_the_size_limit_is_a_parse_error() -> None:
    with pytest.raises(ParseError) as info:
        read_csv(b"a\n" + b"x" * 200_000)

    assert info.value.format == "csv"


def test_read_csv_is_idempotent() -> None:
    data = b"a;b\n1;2\n"

    assert read_csv(data) == read_csv(data)


# --- read_zip ----------------------------------------------------------------


def test_read_zip_parses_each_member_by_extension_in_archive_order() -> None:
    data = _zip(
        ("b.TXT", b"raw"),
        ("a.JSON", b'{"k": 1}'),
        ("c.csv", b"x,y\n1,2\n"),
        ("d.xml", b"<r/>"),
    )

    members = read_zip(data)

    assert list(members) == ["b.TXT", "a.JSON", "c.csv", "d.xml"]
    assert members["b.TXT"] == b"raw"
    assert members["a.JSON"] == {"k": 1}
    assert members["c.csv"] == [{"x": "1", "y": "2"}]
    d = members["d.xml"]
    assert isinstance(d, ET.Element)
    assert d.tag == "r"


def test_read_zip_skips_directory_entries() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.mkdir("dir")
        archive.writestr("dir/A.JSON", b"{}")

    assert read_zip(buffer.getvalue()) == {"dir/A.JSON": {}}


def test_read_zip_of_an_empty_archive_is_empty() -> None:
    assert read_zip(_zip()) == {}


def test_read_zip_recurses_into_a_nested_archive() -> None:
    inner = _zip(("x.xml", b"<x/>"))
    outer = _zip(("inner.zip", inner), ("notes.txt", b"n"))

    members = read_zip(outer)

    nested = members["inner.zip"]
    assert isinstance(nested, dict)
    element = nested["x.xml"]
    assert isinstance(element, ET.Element)
    assert element.tag == "x"
    assert members["notes.txt"] == b"n"


def test_read_zip_keeps_non_ascii_member_names() -> None:
    assert read_zip(_zip(("måling.csv", b"a\n1\n"))) == {"måling.csv": [{"a": "1"}]}


def test_read_zip_duplicate_member_names_keep_the_last() -> None:
    # Documented: a later member silently replaces an earlier one of the same name.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.json", b'{"v": 1}')
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("a.json", b'{"v": 2}')

    assert read_zip(buffer.getvalue()) == {"a.json": {"v": 2}}


@pytest.mark.parametrize("data", [b"", b"hello", b"PK\x03\x04trunc"])
def test_read_zip_rejects_something_that_is_not_an_archive(data: bytes) -> None:
    with pytest.raises(ParseError) as info:
        read_zip(data)

    assert info.value.format == "zip"
    assert str(info.value).startswith("zip: ")


def test_read_zip_malformed_member_names_the_member_with_one_prefix() -> None:
    data = _zip(("ok.csv", b"a\n1\n"), ("bad.json", b"{"))

    with pytest.raises(ParseError) as info:
        read_zip(data)

    assert info.value.format == "json"
    assert str(info.value).startswith("json: member 'bad.json': ")
    assert str(info.value).count("json: ") == 1
    assert isinstance(info.value.__cause__, ParseError)


def test_read_zip_nested_error_names_both_members() -> None:
    inner = _zip(("b.json", b"{"))
    outer = _zip(("inner.zip", inner))

    with pytest.raises(ParseError) as info:
        read_zip(outer)

    assert info.value.format == "json"
    assert "member 'inner.zip': member 'b.json'" in str(info.value)


def test_read_zip_corrupt_member_data_is_a_parse_error_naming_the_member() -> None:
    data = bytearray(_zip(("a.txt", b"payload-payload")))
    at = data.index(b"payload")
    data[at] ^= 0xFF  # stored member: the CRC no longer matches

    with pytest.raises(ParseError, match="a.txt") as info:
        read_zip(bytes(data))

    assert info.value.format == "zip"


def test_read_zip_never_leaks_other_exceptions_for_damaged_archives() -> None:
    valid = _zip(("a.json", b'{"a": 1}'), ("b.csv", b"a\n1\n"))
    damaged = [valid[:n] for n in range(len(valid))]
    for at in range(len(valid)):
        flipped = bytearray(valid)
        flipped[at] ^= 0xFF
        damaged.append(bytes(flipped))

    for data in damaged:
        with contextlib.suppress(ParseError):
            read_zip(data)


# --- format_from_content_type / format_from_filename ------------------------


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("application/json", "json"),
        ("text/json", "json"),
        ("APPLICATION/JSON; charset=UTF-8", "json"),
        (" text/json ", "json"),
        ("application/vnd.api+json", "json"),
        ("application/json;", "json"),
        ("application/xml", "xml"),
        ("text/xml", "xml"),
        ("application/atom+xml", "xml"),
        ("image/svg+xml", "xml"),
        ("text/csv", "csv"),
        ("application/csv", "csv"),
        ("text/csv;header=present", "csv"),
        ("application/zip", "zip"),
        ("application/x-zip-compressed", "zip"),
        ("application/x-zip", "zip"),
    ],
)
def test_format_from_content_type_maps_each_media_type(
    content_type: str, expected: str
) -> None:
    assert format_from_content_type(content_type) == expected


@pytest.mark.parametrize(
    "content_type",
    [
        "",
        "+json",
        "json",
        "application/jsonx",
        "application/json-seq",
        "text/plain",
        "application/octet-stream",
        "application/json, text/plain",
    ],
)
def test_format_from_content_type_returns_none_for_anything_else(
    content_type: str,
) -> None:
    assert format_from_content_type(content_type) is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.json", "json"),
        ("A.JSON", "json"),
        (".json", "json"),
        ("dir/x.csv", "csv"),
        ("dir/A.CSV", "csv"),
        ("x.xml", "xml"),
        ("a.tar.zip", "zip"),
    ],
)
def test_format_from_filename_maps_each_extension(name: str, expected: str) -> None:
    assert format_from_filename(name) == expected


@pytest.mark.parametrize(
    "name", ["a.json.bak", "x.json.txt", "json", "", "a.geojson", "a.jsonl", "a.xml "]
)
def test_format_from_filename_returns_none_for_anything_else(name: str) -> None:
    # Only a dotted suffix counts: "a.geojson" ends in "json" but not in ".json".
    assert format_from_filename(name) is None


# --- read_bytes --------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "data"),
    [
        ("json", b'{"a": 1}'),
        ("xml", b"<a/>"),
        ("csv", b"a,b\n1,2\n"),
        ("zip", _zip(("a.json", b"{}"))),
    ],
)
def test_read_bytes_dispatches_to_the_reader_for_each_format(
    fmt: str, data: bytes
) -> None:
    parsed = read_bytes(data, fmt)  # type: ignore[arg-type]  # str from parametrize

    reader = {"json": read_json, "xml": read_xml, "csv": read_csv, "zip": read_zip}
    expected = reader[fmt](data)
    if isinstance(parsed, ET.Element):
        assert isinstance(expected, ET.Element)
        assert parsed.tag == expected.tag
    else:
        assert parsed == expected


def test_read_bytes_covers_every_listed_format() -> None:
    assert set(FORMATS) == {"json", "xml", "csv", "zip"}


def test_read_bytes_csv_drops_a_bom() -> None:
    assert read_bytes(b"\xef\xbb\xbfa\n1\n", "csv") == [{"a": "1"}]


@pytest.mark.parametrize("fmt", ["JSON", "yaml", "", None])
def test_read_bytes_rejects_an_unknown_format_as_a_plain_value_error(
    fmt: object,
) -> None:
    with pytest.raises(ValueError, match="fmt must be one of") as info:
        read_bytes(b"{}", fmt)  # type: ignore[arg-type]  # deliberately invalid

    assert not isinstance(info.value, ParseError)
    assert repr(fmt) in str(info.value)


# --- read_response -----------------------------------------------------------


@pytest.mark.parametrize(
    ("content_type", "body", "expected"),
    [
        ("application/json", b'{"a": 1}', {"a": 1}),
        ("text/csv", b"a,b\n1,2\n", [{"a": "1", "b": "2"}]),
        ("application/zip", _zip(("a.json", b"[1]")), {"a.json": [1]}),
    ],
)
def test_read_response_picks_the_format_from_content_type(
    content_type: str, body: bytes, expected: object
) -> None:
    assert read_response(_response(content_type, body)) == expected


def test_read_response_reads_xml_by_content_type() -> None:
    root = read_response(_response("application/xml", b"<a/>"))

    assert isinstance(root, ET.Element)
    assert root.tag == "a"


@pytest.mark.parametrize(
    "content_type", [None, "text/plain", "application/octet-stream"]
)
def test_read_response_raises_when_it_cannot_tell_the_format(
    content_type: str | None,
) -> None:
    with pytest.raises(ParseError, match="Content-Type") as info:
        read_response(_response(content_type, b"{}"))

    assert info.value.format is None
    assert not str(info.value).startswith("None")
    assert repr(content_type or "") in str(info.value)


def test_read_response_explicit_format_beats_the_header() -> None:
    assert read_response(_response("application/json", b"a\n1\n"), "csv") == [
        {"a": "1"}
    ]
    root = read_response(_response("application/json", b"<a/>"), "xml")
    assert isinstance(root, ET.Element)
    assert root.tag == "a"


def test_read_response_explicit_format_works_without_a_header() -> None:
    assert read_response(_response(None, b'{"a": 1}'), "json") == {"a": 1}


def test_read_response_rejects_an_unknown_explicit_format() -> None:
    with pytest.raises(ValueError, match="'yaml'") as info:
        read_response(_response("application/json", b"{}"), "yaml")  # type: ignore[arg-type]  # deliberately invalid

    assert not isinstance(info.value, ParseError)


def test_read_response_csv_uses_the_declared_charset() -> None:
    body = "navn\nKøge\n".encode("latin-1")

    rows = read_response(_response("text/csv; charset=latin-1", body))

    assert rows == [{"navn": "Køge"}]


def test_read_response_csv_charset_also_applies_to_an_explicit_format() -> None:
    body = "navn\nKøge\n".encode("latin-1")

    rows = read_response(
        _response("application/octet-stream; charset=latin-1", body), "csv"
    )

    assert rows == [{"navn": "Køge"}]


@pytest.mark.parametrize("charset", ["utf-8", "UTF-8", "utf8"])
def test_read_response_csv_drops_a_bom_when_the_charset_is_utf8(charset: str) -> None:
    body = b"\xef\xbb\xbfa,b\n1,2\n"

    from_response = read_response(_response(f"text/csv; charset={charset}", body))

    assert from_response == [{"a": "1", "b": "2"}] == read_csv(body)


def test_read_response_csv_without_charset_drops_a_bom() -> None:
    assert read_response(_response("text/csv", b"\xef\xbb\xbfa\n1\n")) == [{"a": "1"}]


def test_read_response_csv_unknown_charset_is_a_parse_error_naming_it() -> None:
    with pytest.raises(ParseError, match="bogus") as info:
        read_response(_response("text/csv; charset=bogus", b"a\n1\n"))

    assert info.value.format == "csv"


def test_read_response_json_ignores_the_charset_and_requires_utf8() -> None:
    # Documented: RFC 8259 fixes JSON as UTF-8, so a declared latin-1 is not applied.
    body = '{"a": "æ"}'.encode("latin-1")

    with pytest.raises(ParseError) as info:
        read_response(_response("application/json; charset=iso-8859-1", body))

    assert info.value.format == "json"


def test_read_response_malformed_body_names_the_format() -> None:
    with pytest.raises(ParseError) as info:
        read_response(_response("application/xml", b"<a>"))

    assert info.value.format == "xml"


# --- A9: dependencies --------------------------------------------------------


def test_httpx_and_pandas_are_the_only_runtime_dependencies() -> None:
    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    dependencies = project["project"]["dependencies"]

    names = sorted(re.split(r"[<>=!~\s\[]", dep, maxsplit=1)[0] for dep in dependencies)
    assert names == ["httpx", "pandas"]
