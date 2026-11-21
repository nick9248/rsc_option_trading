"""
API connection handler for making HTTP requests.

Provides base methods for connecting to APIs, making requests, and handling responses.
"""

import logging
import time
from typing import Any, Dict, Optional

import requests
from requests.exceptions import RequestException, Timeout

from coding.core.api.exceptions import (
    ConnectionError,
    RequestError,
    ApiUnavailableError
)
from coding.core.endpoints.endpoint_definition import EndpointDefinition, HttpMethod


logger = logging.getLogger(__name__)

RATE_LIMIT_STATUS = 429
RATE_LIMIT_BACKOFF_SECONDS = 1.0
RATE_LIMIT_MAXIMUM_WAIT_SECONDS = 10.0
"""Waited after a 429 (Too Many Requests) before the next attempt, times the attempt number: 1 s, then 2 s (the server's Retry-After
when it sends one, at most 10 s). Retrying within milliseconds (as before) cannot succeed, the limit has not recovered. The wait
blocks the calling thread: the analysis runs in a worker thread, never in the GUI thread."""


class ApiConnection:
    """
    Handles HTTP connections to an API.

    Provides methods for testing connectivity, making requests, and handling errors.
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 30,
        max_retries: int = 3
    ):
        """
        Initialize the API connection.

        Args:
            base_url: The base URL for the API.
            timeout: Request timeout in seconds.
            max_retries: Maximum number of retry attempts for failed requests.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    @staticmethod
    def _rate_limit_wait(reply: Any, attempt: int) -> float:
        """Seconds to wait after a 429: the server's Retry-After when it is a number, else the growing default; at most the maximum."""
        header = getattr(reply, "headers", None) or {}
        try:
            requested = float(header.get("Retry-After"))
        except (TypeError, ValueError):
            requested = RATE_LIMIT_BACKOFF_SECONDS * attempt
        return min(max(requested, 0.0), RATE_LIMIT_MAXIMUM_WAIT_SECONDS)

    def check_connectivity(self, test_endpoint: EndpointDefinition) -> bool:
        """
        Test if the API is available and responding.

        Args:
            test_endpoint: The endpoint to use for connectivity testing.

        Returns:
            True if the API is available, False otherwise.

        Raises:
            ApiUnavailableError: If the API is not available after retries.
        """
        logger.info(f"Checking API connectivity at {self.base_url}")

        try:
            response = self.fetch(test_endpoint)
            if response is not None:
                logger.info("API connectivity check successful")
                return True
        except (ConnectionError, RequestError) as error:
            logger.error(f"API connectivity check failed: {error}")
            raise ApiUnavailableError(
                f"API is not available: {error}",
                details={"base_url": self.base_url}
            )

        return False

    def fetch(
        self,
        endpoint: EndpointDefinition,
        parameters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make a request to the specified endpoint.

        Args:
            endpoint: The endpoint definition to call.
            parameters: Optional dictionary of query parameters.

        Returns:
            The raw JSON response from the API.

        Raises:
            ConnectionError: If unable to connect to the API.
            RequestError: If the request fails.
        """
        url = endpoint.get_full_url(self.base_url)
        parameters = parameters or {}

        logger.debug(f"Fetching {endpoint.path} with parameters: {parameters}")

        for attempt in range(1, self.max_retries + 1):
            try:
                if endpoint.method == HttpMethod.GET:
                    response = self.session.get(
                        url,
                        params=parameters,
                        timeout=self.timeout
                    )
                else:
                    response = self.session.post(
                        url,
                        json=parameters,
                        timeout=self.timeout
                    )

                response.raise_for_status()

                logger.debug(f"Request successful: {response.status_code}")
                return response.json()

            except Timeout:
                logger.warning(f"Request timeout (attempt {attempt}/{self.max_retries})")
                if attempt == self.max_retries:
                    raise ConnectionError(
                        f"Request timed out after {self.max_retries} attempts",
                        details={"url": url, "timeout": self.timeout}
                    )

            except RequestException as error:
                logger.warning(f"Request failed (attempt {attempt}/{self.max_retries}): {error}")
                reply = getattr(error, "response", None)
                status_code = getattr(reply, "status_code", None)
                if status_code == RATE_LIMIT_STATUS and attempt < self.max_retries:
                    time.sleep(self._rate_limit_wait(reply, attempt))
                if attempt == self.max_retries:
                    raise RequestError(
                        f"Request failed: {error}",
                        status_code=status_code,
                        details={"url": url, "parameters": parameters}
                    )

        raise RequestError(f"Request failed after {self.max_retries} attempts")

    def close(self) -> None:
        """Close the session and release resources."""
        logger.debug("Closing API connection")
        self.session.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False
