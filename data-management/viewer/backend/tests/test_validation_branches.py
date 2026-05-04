"""Branch-coverage tests for src/api/validation.py dependency factories."""

import pytest
from fastapi import HTTPException

from src.api.validation import (
    SanitizedModel,
    _parse_int_csv,
    _sanitize_nested_value,
    path_int_param,
    path_string_param,
    query_bool_param,
    query_csv_ints_param,
    query_int_param,
    query_string_param,
    range_header_param,
    validate_safe_string,
)


class TestSanitizeNested:
    def test_list_tuple_set_dict_recursion(self):
        result = _sanitize_nested_value(["a\nb", ("c\rd",), {"e\nf"}, {"k\nk": "v\rv"}])
        assert result == ["ab", ("cd",), {"ef"}, {"kk": "vv"}]

    def test_passthrough_non_string(self):
        assert _sanitize_nested_value(42) == 42


class TestSanitizedModel:
    def test_strips_crlf_in_nested_fields(self):
        class M(SanitizedModel):
            name: str
            tags: list[str]
            meta: dict[str, str]

        m = M(name="a\nb", tags=["x\ry"], meta={"k\nk": "v\rv"})
        assert m.name == "ab"
        assert m.tags == ["xy"]
        assert m.meta == {"kk": "vv"}


class TestValidateSafeString:
    @pytest.mark.parametrize("bad", ["a/b", "a\\b", ".", "..", "x\x00y"])
    def test_rejects_dangerous(self, bad):
        with pytest.raises(HTTPException) as exc:
            validate_safe_string(bad, label="thing")
        assert exc.value.status_code == 400

    def test_rejects_empty_when_not_allowed(self):
        with pytest.raises(HTTPException) as exc:
            validate_safe_string("   ", label="thing")
        assert "cannot be empty" in exc.value.detail

    def test_allow_empty_passes(self):
        assert validate_safe_string("", label="thing", allow_empty=True) == ""

    def test_pattern_string_compiled(self):
        assert validate_safe_string("abc", pattern=r"^[a-z]+$") == "abc"
        with pytest.raises(HTTPException):
            validate_safe_string("ABC", pattern=r"^[a-z]+$")


class TestDependencyFactories:
    def test_path_string_dependency_validates(self):
        dep = path_string_param("name", label="name")
        assert dep(value="ok") == "ok"
        with pytest.raises(HTTPException):
            dep(value="bad/name")

    def test_query_string_none_returns_none(self):
        dep = query_string_param("q", default=None)
        assert dep(value=None) is None
        assert dep(value="ok") == "ok"

    def test_path_int_dependency_returns_value(self):
        dep = path_int_param("n")
        assert dep(value=5) == 5

    def test_query_int_dependency_returns_value(self):
        dep = query_int_param("n", default=None)
        assert dep(value=None) is None
        assert dep(value=7) == 7

    def test_query_bool_dependency_returns_value(self):
        dep = query_bool_param("b", default=None)
        assert dep(value=True) is True
        assert dep(value=None) is None

    def test_query_csv_ints_optional_none_list_and_set(self):
        dep_list = query_csv_ints_param("ids", required=False)
        assert dep_list(raw_value=None) == []
        dep_set = query_csv_ints_param("ids", required=False, as_set=True)
        assert dep_set(raw_value=None) == set()
        assert dep_set(raw_value="1,2,2") == {1, 2}

    def test_query_csv_ints_required_parses(self):
        dep = query_csv_ints_param("ids")
        assert dep(raw_value="1,2,3") == [1, 2, 3]


class TestParseIntCsv:
    def test_empty_raises(self):
        with pytest.raises(HTTPException) as exc:
            _parse_int_csv("  ,  ", "ids")
        assert "at least one integer" in exc.value.detail

    def test_invalid_format_raises(self):
        with pytest.raises(HTTPException) as exc:
            _parse_int_csv("1,abc", "ids")
        assert "Invalid ids format" in exc.value.detail


class TestRangeHeader:
    def test_none_returns_none_pair(self):
        dep = range_header_param()
        assert dep(header_value=None) == (None, None)

    def test_no_bytes_prefix_returns_none_pair(self):
        dep = range_header_param()
        assert dep(header_value="items=0-10") == (None, None)

    def test_missing_start_raises(self):
        dep = range_header_param()
        with pytest.raises(HTTPException):
            dep(header_value="bytes=-10")

    def test_end_less_than_start_raises(self):
        dep = range_header_param()
        with pytest.raises(HTTPException):
            dep(header_value="bytes=10-5")

    def test_non_numeric_raises(self):
        dep = range_header_param()
        with pytest.raises(HTTPException):
            dep(header_value="bytes=abc-xyz")

    def test_open_ended(self):
        dep = range_header_param()
        assert dep(header_value="bytes=100-") == (100, None)

    def test_bounded(self):
        dep = range_header_param()
        assert dep(header_value="bytes=0-9") == (0, 10)
