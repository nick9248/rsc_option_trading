"""
Endpoint definition class for API endpoints.

Provides a structured way to define API endpoints with their metadata.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any


class HttpMethod(Enum):
    """HTTP methods supported by the API."""
    GET = "GET"
    POST = "POST"


@dataclass
class EndpointParameter:
    """
    Definition of an endpoint parameter.

    Attributes:
        name: Parameter name.
        required: Whether the parameter is required.
        description: Description of what the parameter does.
        parameter_type: Expected type of the parameter (str, int, float, bool).
        default_value: Default value if not provided.
        allowed_values: List of allowed values for enum-like parameters.
    """
    name: str
    required: bool
    description: str
    parameter_type: str = "str"
    default_value: Optional[Any] = None
    allowed_values: Optional[List[Any]] = None


@dataclass
class EndpointDefinition:
    """
    Definition of an API endpoint.

    Attributes:
        path: The endpoint path (e.g., '/public/test').
        description: Human-readable description of what the endpoint does.
        method: HTTP method (GET or POST).
        parameters: List of parameters the endpoint accepts.
        requires_authentication: Whether the endpoint requires authentication.
    """
    path: str
    description: str
    method: HttpMethod = HttpMethod.GET
    parameters: List[EndpointParameter] = field(default_factory=list)
    requires_authentication: bool = False

    def get_full_url(self, base_url: str) -> str:
        """
        Get the full URL for this endpoint.

        Args:
            base_url: The base URL of the API.

        Returns:
            Complete URL for the endpoint.
        """
        base_url = base_url.rstrip("/")
        return f"{base_url}{self.path}"

    def get_required_parameters(self) -> List[EndpointParameter]:
        """
        Get list of required parameters.

        Returns:
            List of required EndpointParameter objects.
        """
        return [param for param in self.parameters if param.required]

    def validate_parameters(self, provided_params: Dict[str, Any]) -> List[str]:
        """
        Validate provided parameters against endpoint definition.

        Args:
            provided_params: Dictionary of parameter names to values.

        Returns:
            List of validation error messages. Empty list if valid.
        """
        errors = []

        for param in self.parameters:
            if param.required and param.name not in provided_params:
                errors.append(f"Missing required parameter: {param.name}")
                continue

            if param.name in provided_params:
                value = provided_params[param.name]

                if param.allowed_values is not None and value not in param.allowed_values:
                    errors.append(
                        f"Invalid value for {param.name}: {value}. "
                        f"Allowed values: {param.allowed_values}"
                    )

        return errors
