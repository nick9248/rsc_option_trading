"""
Unit tests for endpoint definition module.
"""

import pytest

from coding.core.endpoints.endpoint_definition import (
    EndpointDefinition,
    EndpointParameter,
    HttpMethod
)


class TestEndpointParameter:
    """Tests for EndpointParameter dataclass."""

    def test_create_required_parameter(self):
        """Test creating a required parameter."""
        param = EndpointParameter(
            name="currency",
            required=True,
            description="The currency symbol"
        )

        assert param.name == "currency"
        assert param.required is True
        assert param.description == "The currency symbol"
        assert param.parameter_type == "str"
        assert param.default_value is None
        assert param.allowed_values is None

    def test_create_parameter_with_allowed_values(self):
        """Test creating a parameter with allowed values."""
        param = EndpointParameter(
            name="kind",
            required=False,
            description="Instrument type",
            allowed_values=["option", "future", "spot"]
        )

        assert param.allowed_values == ["option", "future", "spot"]


class TestEndpointDefinition:
    """Tests for EndpointDefinition dataclass."""

    def test_create_endpoint(self):
        """Test creating an endpoint definition."""
        endpoint = EndpointDefinition(
            path="/public/test",
            description="Test endpoint"
        )

        assert endpoint.path == "/public/test"
        assert endpoint.description == "Test endpoint"
        assert endpoint.method == HttpMethod.GET
        assert endpoint.parameters == []
        assert endpoint.requires_authentication is False

    def test_get_full_url(self):
        """Test generating full URL from endpoint."""
        endpoint = EndpointDefinition(
            path="/public/test",
            description="Test endpoint"
        )

        url = endpoint.get_full_url("https://api.example.com")
        assert url == "https://api.example.com/public/test"

        url_with_slash = endpoint.get_full_url("https://api.example.com/")
        assert url_with_slash == "https://api.example.com/public/test"

    def test_get_required_parameters(self):
        """Test getting required parameters."""
        endpoint = EndpointDefinition(
            path="/public/get_instruments",
            description="Get instruments",
            parameters=[
                EndpointParameter(name="currency", required=True, description="Currency"),
                EndpointParameter(name="kind", required=False, description="Kind"),
                EndpointParameter(name="expired", required=False, description="Include expired"),
            ]
        )

        required = endpoint.get_required_parameters()
        assert len(required) == 1
        assert required[0].name == "currency"

    def test_validate_parameters_missing_required(self):
        """Test validation fails for missing required parameter."""
        endpoint = EndpointDefinition(
            path="/public/get_instruments",
            description="Get instruments",
            parameters=[
                EndpointParameter(name="currency", required=True, description="Currency"),
            ]
        )

        errors = endpoint.validate_parameters({})
        assert len(errors) == 1
        assert "Missing required parameter: currency" in errors[0]

    def test_validate_parameters_invalid_value(self):
        """Test validation fails for invalid allowed value."""
        endpoint = EndpointDefinition(
            path="/public/get_instruments",
            description="Get instruments",
            parameters=[
                EndpointParameter(
                    name="kind",
                    required=True,
                    description="Kind",
                    allowed_values=["option", "future"]
                ),
            ]
        )

        errors = endpoint.validate_parameters({"kind": "invalid"})
        assert len(errors) == 1
        assert "Invalid value" in errors[0]

    def test_validate_parameters_success(self):
        """Test validation passes with valid parameters."""
        endpoint = EndpointDefinition(
            path="/public/get_instruments",
            description="Get instruments",
            parameters=[
                EndpointParameter(
                    name="currency",
                    required=True,
                    description="Currency",
                    allowed_values=["ETH", "BTC"]
                ),
            ]
        )

        errors = endpoint.validate_parameters({"currency": "ETH"})
        assert errors == []
