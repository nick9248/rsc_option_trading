"""
Unit tests for response parser module.
"""

import pytest

from coding.core.api.response_parser import ResponseParser
from coding.core.api.exceptions import ResponseParseError


class TestResponseParser:
    """Tests for ResponseParser class."""

    def test_extract_result_success(self):
        """Test extracting result from valid response."""
        response = {
            "jsonrpc": "2.0",
            "result": {"version": "1.2.26"},
            "usIn": 123456789,
            "usOut": 123456790,
            "testnet": False
        }

        result = ResponseParser.extract_result(response)
        assert result == {"version": "1.2.26"}

    def test_extract_result_with_error(self):
        """Test extracting result raises error when API returns error."""
        response = {
            "jsonrpc": "2.0",
            "error": {
                "message": "Invalid currency",
                "code": 10001
            }
        }

        with pytest.raises(ResponseParseError) as exc_info:
            ResponseParser.extract_result(response)

        assert "Invalid currency" in str(exc_info.value)

    def test_extract_result_missing_result(self):
        """Test extracting result raises error when result is missing."""
        response = {"jsonrpc": "2.0"}

        with pytest.raises(ResponseParseError) as exc_info:
            ResponseParser.extract_result(response)

        assert "missing 'result' field" in str(exc_info.value)

    def test_extract_result_invalid_type(self):
        """Test extracting result raises error for non-dict input."""
        with pytest.raises(ResponseParseError) as exc_info:
            ResponseParser.extract_result("not a dict")

        assert "expected dictionary" in str(exc_info.value)

    def test_extract_metadata(self):
        """Test extracting metadata from response."""
        response = {
            "jsonrpc": "2.0",
            "result": {},
            "usIn": 123456789,
            "usOut": 123456790,
            "usDiff": 1,
            "testnet": False
        }

        metadata = ResponseParser.extract_metadata(response)
        assert metadata["jsonrpc"] == "2.0"
        assert metadata["us_in"] == 123456789
        assert metadata["us_out"] == 123456790
        assert metadata["us_diff"] == 1
        assert metadata["testnet"] is False

    def test_flatten_dict(self):
        """Test flattening nested dictionary."""
        data = {
            "stats": {
                "high": 100,
                "low": 50
            },
            "name": "test"
        }

        flattened = ResponseParser.flatten_dict(data)
        assert flattened == {
            "stats_high": 100,
            "stats_low": 50,
            "name": "test"
        }

    def test_flatten_dict_deeply_nested(self):
        """Test flattening deeply nested dictionary."""
        data = {
            "level1": {
                "level2": {
                    "value": 42
                }
            }
        }

        flattened = ResponseParser.flatten_dict(data)
        assert flattened == {"level1_level2_value": 42}

    def test_array_to_dicts(self):
        """Test converting array data to dictionaries."""
        data = [
            [1704067200000, 45.5],
            [1704070800000, 46.2],
        ]
        columns = ["timestamp", "volatility"]

        result = ResponseParser.array_to_dicts(data, columns)
        assert len(result) == 2
        assert result[0] == {"timestamp": 1704067200000, "volatility": 45.5}
        assert result[1] == {"timestamp": 1704070800000, "volatility": 46.2}

    def test_array_to_dicts_empty(self):
        """Test converting empty array."""
        result = ResponseParser.array_to_dicts([], ["a", "b"])
        assert result == []

    def test_array_to_dicts_column_mismatch(self):
        """Test array conversion fails with column count mismatch."""
        data = [[1, 2, 3]]
        columns = ["a", "b"]

        with pytest.raises(ResponseParseError) as exc_info:
            ResponseParser.array_to_dicts(data, columns)

        assert "Column count mismatch" in str(exc_info.value)
