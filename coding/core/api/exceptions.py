"""
Custom exceptions for API operations.

Provides specific exception types for different API error scenarios.
"""


class ApiException(Exception):
    """Base exception for all API-related errors."""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConnectionError(ApiException):
    """Raised when unable to connect to the API."""
    pass


class RequestError(ApiException):
    """Raised when the API request fails."""

    def __init__(self, message: str, status_code: int = None, details: dict = None):
        super().__init__(message, details)
        self.status_code = status_code


class ResponseParseError(ApiException):
    """Raised when unable to parse the API response."""
    pass


class SchemaValidationError(ApiException):
    """Raised when the API response does not match the expected schema."""

    def __init__(self, message: str, expected_fields: list = None, actual_fields: list = None):
        super().__init__(message)
        self.expected_fields = expected_fields or []
        self.actual_fields = actual_fields or []


class ApiUnavailableError(ApiException):
    """Raised when the API is not available or returns an error state."""
    pass


class ParameterValidationError(ApiException):
    """Raised when provided parameters are invalid."""
    pass
