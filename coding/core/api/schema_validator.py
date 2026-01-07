"""
Schema validator for API responses.

Validates that API responses match expected structure and field types.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, Union

from coding.core.api.exceptions import SchemaValidationError


logger = logging.getLogger(__name__)


@dataclass
class FieldSchema:
    """
    Schema definition for a single field.

    Attributes:
        name: Field name.
        field_type: Expected Python type or list of allowed types.
        required: Whether the field must be present.
        nullable: Whether the field can be None.
        nested_schema: For dict fields, the schema of nested fields.
        item_schema: For list fields, the schema of list items.
    """
    name: str
    field_type: Union[Type, List[Type]]
    required: bool = True
    nullable: bool = False
    nested_schema: Optional[List["FieldSchema"]] = None
    item_schema: Optional[List["FieldSchema"]] = None


@dataclass
class ResponseSchema:
    """
    Schema definition for an API response.

    Attributes:
        name: Descriptive name for the schema.
        result_type: Expected type of the result field (dict, list, etc.).
        fields: List of field schemas for the result content.
        description: Human-readable description of the response.
    """
    name: str
    result_type: Type
    fields: List[FieldSchema] = field(default_factory=list)
    description: str = ""


class SchemaValidator:
    """
    Validates API responses against defined schemas.

    Provides methods to check field presence, types, and structure.
    """

    def __init__(self, strict_mode: bool = False):
        """
        Initialize the schema validator.

        Args:
            strict_mode: If True, fail on unexpected fields. If False, only warn.
        """
        self.strict_mode = strict_mode

    def validate(
        self,
        data: Any,
        schema: ResponseSchema
    ) -> List[str]:
        """
        Validate data against a schema.

        Args:
            data: The data to validate (typically the 'result' from API response).
            schema: The schema to validate against.

        Returns:
            List of validation warning/error messages. Empty if valid.

        Raises:
            SchemaValidationError: If validation fails in strict mode.
        """
        errors = []

        if not isinstance(data, schema.result_type):
            errors.append(
                f"Result type mismatch: expected {schema.result_type.__name__}, "
                f"got {type(data).__name__}"
            )
            if self.strict_mode:
                raise SchemaValidationError(
                    f"Schema validation failed for {schema.name}",
                    expected_fields=[f.name for f in schema.fields],
                    actual_fields=list(data.keys()) if isinstance(data, dict) else []
                )
            return errors

        if schema.result_type == dict:
            errors.extend(self._validate_dict(data, schema.fields, schema.name))
        elif schema.result_type == list and schema.fields and len(data) > 0:
            for index, item in enumerate(data[:3]):
                item_errors = self._validate_dict(item, schema.fields, f"{schema.name}[{index}]")
                errors.extend(item_errors)

        if errors:
            logger.warning(f"Schema validation warnings for {schema.name}: {errors}")
            if self.strict_mode:
                raise SchemaValidationError(
                    f"Schema validation failed for {schema.name}: {'; '.join(errors)}"
                )
        else:
            logger.debug(f"Schema validation passed for {schema.name}")

        return errors

    def _validate_dict(
        self,
        data: Dict[str, Any],
        field_schemas: List[FieldSchema],
        context: str
    ) -> List[str]:
        """
        Validate a dictionary against field schemas.

        Args:
            data: Dictionary to validate.
            field_schemas: List of field schemas.
            context: Context string for error messages.

        Returns:
            List of validation error messages.
        """
        errors = []
        expected_fields = {f.name for f in field_schemas}
        actual_fields = set(data.keys())

        for field_schema in field_schemas:
            field_name = field_schema.name

            if field_name not in data:
                if field_schema.required:
                    errors.append(f"Missing required field: {context}.{field_name}")
                continue

            value = data[field_name]

            if value is None:
                if not field_schema.nullable:
                    errors.append(f"Null value for non-nullable field: {context}.{field_name}")
                continue

            expected_types = field_schema.field_type
            if not isinstance(expected_types, list):
                expected_types = [expected_types]

            if not any(isinstance(value, t) for t in expected_types):
                errors.append(
                    f"Type mismatch for {context}.{field_name}: "
                    f"expected {[t.__name__ for t in expected_types]}, "
                    f"got {type(value).__name__}"
                )
                continue

            if field_schema.nested_schema and isinstance(value, dict):
                nested_errors = self._validate_dict(
                    value,
                    field_schema.nested_schema,
                    f"{context}.{field_name}"
                )
                errors.extend(nested_errors)

            if field_schema.item_schema and isinstance(value, list) and len(value) > 0:
                for index, item in enumerate(value[:3]):
                    if isinstance(item, dict):
                        item_errors = self._validate_dict(
                            item,
                            field_schema.item_schema,
                            f"{context}.{field_name}[{index}]"
                        )
                        errors.extend(item_errors)

        if self.strict_mode:
            unexpected = actual_fields - expected_fields
            if unexpected:
                errors.append(f"Unexpected fields in {context}: {unexpected}")

        return errors

    def check_fields_present(
        self,
        data: Dict[str, Any],
        required_fields: List[str]
    ) -> List[str]:
        """
        Quick check that required fields are present.

        Args:
            data: Dictionary to check.
            required_fields: List of field names that must be present.

        Returns:
            List of missing field names.
        """
        missing = [f for f in required_fields if f not in data]
        if missing:
            logger.warning(f"Missing fields: {missing}")
        return missing
