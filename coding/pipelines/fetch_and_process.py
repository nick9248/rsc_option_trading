"""
Fetch and process pipeline for API data retrieval.

Orchestrates the flow of:
1. Parameter validation
2. Data fetching from endpoint
3. Response parsing
4. Schema validation
5. Optional CSV export
"""

import logging
from typing import Any, Dict, List, Optional

from coding.core.api.connection import ApiConnection
from coding.core.api.exceptions import ParameterValidationError
from coding.core.api.response_parser import ResponseParser
from coding.core.api.schema_validator import SchemaValidator
from coding.core.endpoints.endpoint_definition import EndpointDefinition
from coding.core.schemas.deribit_schemas import DeribitSchemas


logger = logging.getLogger(__name__)


def fetch_and_process(
    connection: ApiConnection,
    parser: ResponseParser,
    validator: SchemaValidator,
    endpoint: EndpointDefinition,
    parameters: Optional[Dict[str, Any]] = None,
    validate_responses: bool = True,
    save_to_csv: bool = False,
    csv_filename: Optional[str] = None,
    csv_subdirectory: Optional[str] = None
) -> Any:
    """
    Pipeline to fetch, parse, validate, and optionally save API data.

    Args:
        connection: API connection instance for making requests.
        parser: Response parser for extracting and converting data.
        validator: Schema validator for response validation.
        endpoint: The endpoint definition to call.
        parameters: Query parameters for the request.
        validate_responses: Whether to validate responses against schemas.
        save_to_csv: Whether to save results to CSV.
        csv_filename: Filename for CSV output.
        csv_subdirectory: Subdirectory within output/data.

    Returns:
        Parsed and validated result data.

    Raises:
        ParameterValidationError: If parameters are invalid for the endpoint.
        ApiConnectionError: If the API request fails.
        SchemaValidationError: If validation fails in strict mode.
    """
    parameters = parameters or {}

    validation_errors = endpoint.validate_parameters(parameters)
    if validation_errors:
        raise ParameterValidationError(
            f"Invalid parameters: {'; '.join(validation_errors)}"
        )

    logger.info(f"Fetching {endpoint.path}")
    response = connection.fetch(endpoint, parameters)

    result = parser.extract_result(response)

    if validate_responses:
        schema = DeribitSchemas.get_schema_for_endpoint(endpoint.path)
        warnings = validator.validate(result, schema)
        if warnings:
            logger.warning(f"Validation warnings: {warnings}")

    if save_to_csv and csv_filename:
        _save_result_to_csv(parser, result, csv_filename, csv_subdirectory)

    return result


def _save_result_to_csv(
    parser: ResponseParser,
    result: Any,
    csv_filename: str,
    csv_subdirectory: Optional[str] = None
) -> None:
    """
    Save result data to CSV file.

    Args:
        parser: Response parser with CSV export capability.
        result: Data to save.
        csv_filename: Filename for CSV output.
        csv_subdirectory: Subdirectory within output/data.
    """
    try:
        if isinstance(result, list):
            parser.to_csv(result, csv_filename, csv_subdirectory)
        elif isinstance(result, dict):
            if "data" in result and isinstance(result["data"], list):
                parser.to_csv(result["data"], csv_filename, csv_subdirectory)
            else:
                parser.to_csv(result, csv_filename, csv_subdirectory)
    except Exception as error:
        logger.error(f"Failed to save CSV: {error}")


def main():
    """
    Run the fetch_and_process pipeline from command line.

    Available Endpoints:
    --------------------
    TEST
        Path: /public/test
        Parameters: None

    GET_EXPIRATIONS
        Path: /public/get_expirations
        Parameters:
            - currency (required): ETH, BTC, USDC, USDT, EURR

    GET_INSTRUMENTS
        Path: /public/get_instruments
        Parameters:
            - currency (required): ETH, BTC, USDC, USDT, EURR, any
            - kind (optional): future, option, spot, future_combo, option_combo
            - expired (optional): true, false

    GET_BOOK_SUMMARY_BY_CURRENCY
        Path: /public/get_book_summary_by_currency
        Parameters:
            - currency (required): ETH, BTC, USDC, USDT, EURR, any
            - kind (optional): future, option, spot, future_combo, option_combo

    TICKER
        Path: /public/ticker
        Parameters:
            - instrument_name (required): e.g., ETH-PERPETUAL, BTC-PERPETUAL

    GET_ORDER_BOOK
        Path: /public/get_order_book
        Parameters:
            - instrument_name (required): e.g., ETH-PERPETUAL
            - depth (optional): 1-10000 (default: 10)

    GET_FUNDING_CHART_DATA
        Path: /public/get_funding_chart_data
        Parameters:
            - instrument_name (required): e.g., ETH-PERPETUAL, BTC-PERPETUAL
            - length (required): 8h, 24h, 1m

    GET_HISTORICAL_VOLATILITY
        Path: /public/get_historical_volatility
        Parameters:
            - currency (required): ETH, BTC

    GET_VOLATILITY_INDEX_DATA
        Path: /public/get_volatility_index_data
        Parameters:
            - currency (required): ETH, BTC
            - resolution (required): seconds (e.g., 3600 for 1 hour)
            - start_timestamp (required): milliseconds
            - end_timestamp (required): milliseconds

    Usage Example:
    --------------
    from coding.core.logging.logging_setup import init_logging
    from coding.service.deribit.deribit_api_service import DeribitApiService

    init_logging(level="INFO")
    service = DeribitApiService()
    result = service.get_instruments(currency="ETH", kind="option")
    """
    # Imports here to avoid circular dependency (service imports pipeline)
    from coding.core.logging.logging_setup import init_logging
    from coding.service.deribit.deribit_api_service import DeribitApiService

    init_logging(level="INFO")
    logger.info("Starting fetch_and_process pipeline")

    with DeribitApiService() as service:
        connectivity = service.check_connectivity()
        logger.info(f"API connectivity: {connectivity}")

        result = service.get_instruments(currency="ETH", kind="option", save_to_csv=True)
        logger.info(f"Fetched {len(result)} instruments")


if __name__ == "__main__":
    main()

