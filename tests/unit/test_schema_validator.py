"""
Unit tests for schema validator module.
"""

import pytest

from coding.core.api.schema_validator import (
    FieldSchema,
    ResponseSchema,
    SchemaValidator
)
from coding.core.api.exceptions import SchemaValidationError


class TestSchemaValidator:
    """Tests for SchemaValidator class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = SchemaValidator(strict_mode=False)
        self.strict_validator = SchemaValidator(strict_mode=True)

    def test_validate_simple_dict(self):
        """Test validating a simple dictionary response."""
        schema = ResponseSchema(
            name="Test",
            result_type=dict,
            fields=[
                FieldSchema(name="version", field_type=str, required=True)
            ]
        )

        data = {"version": "1.0.0"}
        errors = self.validator.validate(data, schema)
        assert errors == []

    def test_validate_missing_required_field(self):
        """Test validation fails for missing required field."""
        schema = ResponseSchema(
            name="Test",
            result_type=dict,
            fields=[
                FieldSchema(name="version", field_type=str, required=True)
            ]
        )

        data = {}
        errors = self.validator.validate(data, schema)
        assert len(errors) == 1
        assert "Missing required field" in errors[0]

    def test_validate_type_mismatch(self):
        """Test validation fails for type mismatch."""
        schema = ResponseSchema(
            name="Test",
            result_type=dict,
            fields=[
                FieldSchema(name="count", field_type=int, required=True)
            ]
        )

        data = {"count": "not an int"}
        errors = self.validator.validate(data, schema)
        assert len(errors) == 1
        assert "Type mismatch" in errors[0]

    def test_validate_multiple_allowed_types(self):
        """Test validation with multiple allowed types."""
        schema = ResponseSchema(
            name="Test",
            result_type=dict,
            fields=[
                FieldSchema(name="price", field_type=[int, float], required=True)
            ]
        )

        data_int = {"price": 100}
        errors = self.validator.validate(data_int, schema)
        assert errors == []

        data_float = {"price": 100.5}
        errors = self.validator.validate(data_float, schema)
        assert errors == []

    def test_validate_nullable_field(self):
        """Test validation passes for null value on nullable field."""
        schema = ResponseSchema(
            name="Test",
            result_type=dict,
            fields=[
                FieldSchema(name="value", field_type=float, required=True, nullable=True)
            ]
        )

        data = {"value": None}
        errors = self.validator.validate(data, schema)
        assert errors == []

    def test_validate_null_on_non_nullable(self):
        """Test validation fails for null on non-nullable field."""
        schema = ResponseSchema(
            name="Test",
            result_type=dict,
            fields=[
                FieldSchema(name="value", field_type=float, required=True, nullable=False)
            ]
        )

        data = {"value": None}
        errors = self.validator.validate(data, schema)
        assert len(errors) == 1
        assert "non-nullable" in errors[0]

    def test_validate_nested_schema(self):
        """Test validation of nested dictionary."""
        schema = ResponseSchema(
            name="Test",
            result_type=dict,
            fields=[
                FieldSchema(
                    name="stats",
                    field_type=dict,
                    required=True,
                    nested_schema=[
                        FieldSchema(name="high", field_type=[int, float], required=True),
                        FieldSchema(name="low", field_type=[int, float], required=True),
                    ]
                )
            ]
        )

        data = {"stats": {"high": 100.5, "low": 50.0}}
        errors = self.validator.validate(data, schema)
        assert errors == []

    def test_validate_list_result(self):
        """Test validation of list response."""
        schema = ResponseSchema(
            name="Instruments",
            result_type=list,
            fields=[
                FieldSchema(name="instrument_name", field_type=str, required=True),
                FieldSchema(name="kind", field_type=str, required=True),
            ]
        )

        data = [
            {"instrument_name": "ETH-PERPETUAL", "kind": "future"},
            {"instrument_name": "ETH-8JAN26-3200-C", "kind": "option"},
        ]
        errors = self.validator.validate(data, schema)
        assert errors == []

    def test_validate_result_type_mismatch(self):
        """Test validation fails when result type doesn't match."""
        schema = ResponseSchema(
            name="Test",
            result_type=dict,
            fields=[]
        )

        data = [1, 2, 3]
        errors = self.validator.validate(data, schema)
        assert len(errors) == 1
        assert "Result type mismatch" in errors[0]

    def test_strict_mode_raises_exception(self):
        """Test strict mode raises exception on validation failure."""
        schema = ResponseSchema(
            name="Test",
            result_type=dict,
            fields=[
                FieldSchema(name="required_field", field_type=str, required=True)
            ]
        )

        data = {}
        with pytest.raises(SchemaValidationError):
            self.strict_validator.validate(data, schema)

    def test_check_fields_present(self):
        """Test quick field presence check."""
        data = {"a": 1, "b": 2}
        missing = self.validator.check_fields_present(data, ["a", "b", "c"])
        assert missing == ["c"]

    def test_check_fields_present_all_present(self):
        """Test field check when all fields present."""
        data = {"a": 1, "b": 2}
        missing = self.validator.check_fields_present(data, ["a", "b"])
        assert missing == []
