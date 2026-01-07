"""
Response parser for API responses.

Handles parsing, extracting, and converting API response data.
"""

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from coding.core.api.exceptions import ResponseParseError
from coding.core.logging.logging_setup import get_project_root


logger = logging.getLogger(__name__)


class ResponseParser:
    """
    Parses and processes API responses.

    Extracts data from JSON-RPC responses and provides conversion utilities.
    """

    @staticmethod
    def extract_result(response: Dict[str, Any]) -> Any:
        """
        Extract the result from a JSON-RPC response.

        Args:
            response: The raw API response dictionary.

        Returns:
            The extracted result data.

        Raises:
            ResponseParseError: If the response format is invalid.
        """
        if not isinstance(response, dict):
            raise ResponseParseError(
                "Invalid response format: expected dictionary",
                details={"received_type": type(response).__name__}
            )

        if "error" in response:
            error = response["error"]
            raise ResponseParseError(
                f"API returned error: {error.get('message', 'Unknown error')}",
                details={"error_code": error.get("code"), "error_data": error.get("data")}
            )

        if "result" not in response:
            raise ResponseParseError(
                "Response missing 'result' field",
                details={"available_fields": list(response.keys())}
            )

        logger.debug("Successfully extracted result from response")
        return response["result"]

    @staticmethod
    def extract_metadata(response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract metadata from the response (timing info, testnet flag).

        Args:
            response: The raw API response dictionary.

        Returns:
            Dictionary containing metadata fields.
        """
        return {
            "jsonrpc": response.get("jsonrpc"),
            "us_in": response.get("usIn"),
            "us_out": response.get("usOut"),
            "us_diff": response.get("usDiff"),
            "testnet": response.get("testnet")
        }

    @staticmethod
    def flatten_dict(
        data: Dict[str, Any],
        parent_key: str = "",
        separator: str = "_"
    ) -> Dict[str, Any]:
        """
        Flatten a nested dictionary into a single-level dictionary.

        Args:
            data: The dictionary to flatten.
            parent_key: Prefix for keys in nested dictionaries.
            separator: Separator between parent and child keys.

        Returns:
            Flattened dictionary.
        """
        items = []
        for key, value in data.items():
            new_key = f"{parent_key}{separator}{key}" if parent_key else key
            if isinstance(value, dict):
                items.extend(
                    ResponseParser.flatten_dict(value, new_key, separator).items()
                )
            else:
                items.append((new_key, value))
        return dict(items)

    @staticmethod
    def to_csv(
        data: Union[List[Dict[str, Any]], Dict[str, Any]],
        filename: str,
        output_subdirectory: Optional[str] = None,
        flatten: bool = True
    ) -> Path:
        """
        Save data to a CSV file in the output/data directory.

        Args:
            data: Data to save. Can be a list of dictionaries or a single dictionary.
            filename: Name of the output file (without extension).
            output_subdirectory: Optional subdirectory within output/data.
            flatten: Whether to flatten nested dictionaries.

        Returns:
            Path to the created CSV file.

        Raises:
            ResponseParseError: If data format is invalid for CSV conversion.
        """
        if isinstance(data, dict):
            data = [data]

        if not data:
            raise ResponseParseError("Cannot save empty data to CSV")

        if not isinstance(data[0], dict):
            raise ResponseParseError(
                "Data must be a list of dictionaries for CSV conversion",
                details={"received_type": type(data[0]).__name__}
            )

        if flatten:
            data = [ResponseParser.flatten_dict(item) for item in data]

        output_directory = get_project_root() / "output" / "data"
        if output_subdirectory:
            output_directory = output_directory / output_subdirectory
        output_directory.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = output_directory / f"{filename}_{timestamp}.csv"

        fieldnames = list(data[0].keys())

        with open(filepath, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)

        logger.info(f"Saved {len(data)} records to {filepath}")
        return filepath

    @staticmethod
    def array_to_dicts(
        data: List[List[Any]],
        column_names: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Convert array-based data to list of dictionaries.

        Useful for endpoints that return data as arrays like [[ts, val], ...].

        Args:
            data: List of arrays.
            column_names: Names for each position in the arrays.

        Returns:
            List of dictionaries with named fields.

        Raises:
            ResponseParseError: If data format doesn't match column names.
        """
        if not data:
            return []

        if len(data[0]) != len(column_names):
            raise ResponseParseError(
                f"Column count mismatch: data has {len(data[0])} columns, "
                f"but {len(column_names)} column names provided",
                details={"expected": len(column_names), "actual": len(data[0])}
            )

        return [dict(zip(column_names, row)) for row in data]
