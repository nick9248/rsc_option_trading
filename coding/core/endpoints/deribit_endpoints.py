"""
Deribit API endpoint definitions.

Contains all public Deribit endpoints used in this project.
"""

from coding.core.endpoints.endpoint_definition import (
    EndpointDefinition,
    EndpointParameter,
    HttpMethod
)


DERIBIT_BASE_URL = "https://www.deribit.com/api/v2"


class DeribitEndpoints:
    """
    Collection of Deribit API endpoint definitions.

    All endpoints are public and do not require authentication.
    """

    TEST = EndpointDefinition(
        path="/public/test",
        description="Tests the API connection and returns the server version.",
        method=HttpMethod.GET,
        parameters=[],
        requires_authentication=False
    )

    GET_EXPIRATIONS = EndpointDefinition(
        path="/public/get_expirations",
        description="Retrieves available expiration dates for options and futures by currency.",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="currency",
                required=True,
                description="The currency symbol (e.g., ETH, BTC).",
                parameter_type="str",
                allowed_values=["ETH", "BTC", "USDC", "USDT", "EURR"]
            )
        ],
        requires_authentication=False
    )

    GET_INSTRUMENTS = EndpointDefinition(
        path="/public/get_instruments",
        description="Retrieves available trading instruments for a currency.",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="currency",
                required=True,
                description="The currency symbol (e.g., ETH, BTC).",
                parameter_type="str",
                allowed_values=["ETH", "BTC", "USDC", "USDT", "EURR", "any"]
            ),
            EndpointParameter(
                name="kind",
                required=False,
                description="Instrument kind filter.",
                parameter_type="str",
                default_value=None,
                allowed_values=["future", "option", "spot", "future_combo", "option_combo"]
            ),
            EndpointParameter(
                name="expired",
                required=False,
                description="Include expired instruments.",
                parameter_type="bool",
                default_value=False
            )
        ],
        requires_authentication=False
    )

    GET_BOOK_SUMMARY_BY_CURRENCY = EndpointDefinition(
        path="/public/get_book_summary_by_currency",
        description="Retrieves order book summary for all instruments of a currency.",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="currency",
                required=True,
                description="The currency symbol (e.g., ETH, BTC).",
                parameter_type="str",
                allowed_values=["ETH", "BTC", "USDC", "USDT", "EURR", "any"]
            ),
            EndpointParameter(
                name="kind",
                required=False,
                description="Instrument kind filter.",
                parameter_type="str",
                default_value=None,
                allowed_values=["future", "option", "spot", "future_combo", "option_combo"]
            )
        ],
        requires_authentication=False
    )

    TICKER = EndpointDefinition(
        path="/public/ticker",
        description="Retrieves current ticker data for a specific instrument.",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="instrument_name",
                required=True,
                description="The instrument name (e.g., ETH-PERPETUAL, ETH-8JAN26-3200-C).",
                parameter_type="str"
            )
        ],
        requires_authentication=False
    )

    GET_ORDER_BOOK = EndpointDefinition(
        path="/public/get_order_book",
        description="Retrieves the order book for a specific instrument.",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="instrument_name",
                required=True,
                description="The instrument name (e.g., ETH-PERPETUAL).",
                parameter_type="str"
            ),
            EndpointParameter(
                name="depth",
                required=False,
                description="Number of price levels to retrieve (1-10000).",
                parameter_type="int",
                default_value=10
            )
        ],
        requires_authentication=False
    )

    GET_FUNDING_CHART_DATA = EndpointDefinition(
        path="/public/get_funding_chart_data",
        description="Retrieves funding rate chart data for perpetual instruments.",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="instrument_name",
                required=True,
                description="The perpetual instrument name (e.g., ETH-PERPETUAL).",
                parameter_type="str"
            ),
            EndpointParameter(
                name="length",
                required=True,
                description="Time period for the data.",
                parameter_type="str",
                allowed_values=["8h", "24h", "1m"]
            )
        ],
        requires_authentication=False
    )

    GET_HISTORICAL_VOLATILITY = EndpointDefinition(
        path="/public/get_historical_volatility",
        description="Retrieves historical volatility data for a currency.",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="currency",
                required=True,
                description="The currency symbol (e.g., ETH, BTC).",
                parameter_type="str",
                allowed_values=["ETH", "BTC"]
            )
        ],
        requires_authentication=False
    )

    GET_VOLATILITY_INDEX_DATA = EndpointDefinition(
        path="/public/get_volatility_index_data",
        description="Retrieves DVOL (volatility index) OHLC data for a currency.",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="currency",
                required=True,
                description="The currency symbol (e.g., ETH, BTC).",
                parameter_type="str",
                allowed_values=["ETH", "BTC"]
            ),
            EndpointParameter(
                name="resolution",
                required=True,
                description="Time resolution in seconds (e.g., 3600 for 1 hour).",
                parameter_type="int"
            ),
            EndpointParameter(
                name="start_timestamp",
                required=True,
                description="Start timestamp in milliseconds.",
                parameter_type="int"
            ),
            EndpointParameter(
                name="end_timestamp",
                required=True,
                description="End timestamp in milliseconds.",
                parameter_type="int"
            )
        ],
        requires_authentication=False
    )

    GET_TRADINGVIEW_CHART_DATA = EndpointDefinition(
        path="/public/get_tradingview_chart_data",
        description="Retrieves historical OHLCV data for an instrument (TradingView format).",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="instrument_name",
                required=True,
                description="The instrument name (e.g., BTC-PERPETUAL, ETH-PERPETUAL).",
                parameter_type="str"
            ),
            EndpointParameter(
                name="resolution",
                required=True,
                description="Time resolution (1, 3, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1D).",
                parameter_type="str",
                allowed_values=["1", "3", "5", "10", "15", "30", "60", "120", "180", "360", "720", "1D"]
            ),
            EndpointParameter(
                name="start_timestamp",
                required=True,
                description="Start timestamp in milliseconds.",
                parameter_type="int"
            ),
            EndpointParameter(
                name="end_timestamp",
                required=True,
                description="End timestamp in milliseconds.",
                parameter_type="int"
            )
        ],
        requires_authentication=False
    )

    GET_LAST_TRADES_BY_CURRENCY = EndpointDefinition(
        path="/public/get_last_trades_by_currency",
        description="Retrieves recent trades for all instruments of a currency.",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="currency",
                required=True,
                description="The currency symbol (e.g., ETH, BTC).",
                parameter_type="str",
                allowed_values=["ETH", "BTC", "USDC", "USDT", "EURR"]
            ),
            EndpointParameter(
                name="kind",
                required=False,
                description="Instrument kind filter.",
                parameter_type="str",
                default_value=None,
                allowed_values=["future", "option", "spot", "future_combo", "option_combo"]
            ),
            EndpointParameter(
                name="count",
                required=False,
                description="Number of trades to retrieve (max 1000).",
                parameter_type="int",
                default_value=10
            )
        ],
        requires_authentication=False
    )

    GET_INDEX_PRICE = EndpointDefinition(
        path="/public/get_index_price",
        description=(
            "Retrieves the current index (spot) price for an index name. "
            "This is the price Deribit's website displays and the price used "
            "to settle/mark option premiums in USD terms — distinct from any "
            "per-expiry future price (see get_book_summary's underlying_price)."
        ),
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="index_name",
                required=True,
                description="The index name (e.g., btc_usd, eth_usd).",
                parameter_type="str",
                allowed_values=[
                    "btc_usd", "eth_usd", "ada_usd", "algo_usd", "avax_usd",
                    "bch_usd", "bnb_usd", "doge_usd", "dot_usd", "link_usd",
                    "ltc_usd", "matic_usd", "near_usd", "shib_usd", "sol_usd",
                    "trx_usd", "uni_usd", "xrp_usd", "usdc_usd"
                ]
            )
        ],
        requires_authentication=False
    )

    GET_LAST_TRADES_BY_CURRENCY_AND_TIME = EndpointDefinition(
        path="/public/get_last_trades_by_currency_and_time",
        description="Retrieves historical trades for a currency within a specific time range.",
        method=HttpMethod.GET,
        parameters=[
            EndpointParameter(
                name="currency",
                required=True,
                description="The currency symbol (e.g., ETH, BTC).",
                parameter_type="str",
                allowed_values=["ETH", "BTC", "USDC", "USDT", "EURR"]
            ),
            EndpointParameter(
                name="kind",
                required=False,
                description="Instrument kind filter.",
                parameter_type="str",
                default_value=None,
                allowed_values=["future", "option", "spot", "future_combo", "option_combo"]
            ),
            EndpointParameter(
                name="start_timestamp",
                required=True,
                description="Start timestamp in milliseconds.",
                parameter_type="int"
            ),
            EndpointParameter(
                name="end_timestamp",
                required=True,
                description="End timestamp in milliseconds.",
                parameter_type="int"
            ),
            EndpointParameter(
                name="count",
                required=False,
                description="Number of trades to retrieve per request (max 1000).",
                parameter_type="int",
                default_value=1000
            ),
            EndpointParameter(
                name="include_old",
                required=False,
                description="Include historical data.",
                parameter_type="bool",
                default_value=True
            )
        ],
        requires_authentication=False
    )

    @classmethod
    def get_all_endpoints(cls) -> list:
        """
        Get all defined endpoints.

        Returns:
            List of all EndpointDefinition objects.
        """
        return [
            cls.TEST,
            cls.GET_EXPIRATIONS,
            cls.GET_INSTRUMENTS,
            cls.GET_BOOK_SUMMARY_BY_CURRENCY,
            cls.TICKER,
            cls.GET_ORDER_BOOK,
            cls.GET_FUNDING_CHART_DATA,
            cls.GET_HISTORICAL_VOLATILITY,
            cls.GET_VOLATILITY_INDEX_DATA,
            cls.GET_TRADINGVIEW_CHART_DATA,
            cls.GET_LAST_TRADES_BY_CURRENCY,
            cls.GET_LAST_TRADES_BY_CURRENCY_AND_TIME,
            cls.GET_INDEX_PRICE
        ]
