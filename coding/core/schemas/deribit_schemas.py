"""
Response schemas for Deribit API endpoints.

Defines expected response structures for validation.
"""

from coding.core.api.schema_validator import FieldSchema, ResponseSchema


class DeribitSchemas:
    """
    Collection of response schemas for Deribit API endpoints.
    """

    WRAPPER = ResponseSchema(
        name="DeribitWrapper",
        result_type=dict,
        description="Common wrapper structure for all Deribit responses.",
        fields=[
            FieldSchema(name="jsonrpc", field_type=str, required=True),
            FieldSchema(name="result", field_type=[dict, list], required=True),
            FieldSchema(name="usIn", field_type=int, required=False),
            FieldSchema(name="usOut", field_type=int, required=False),
            FieldSchema(name="usDiff", field_type=int, required=False),
            FieldSchema(name="testnet", field_type=bool, required=False),
        ]
    )

    TEST = ResponseSchema(
        name="Test",
        result_type=dict,
        description="Response from /public/test endpoint.",
        fields=[
            FieldSchema(name="version", field_type=str, required=True),
        ]
    )

    GET_EXPIRATIONS = ResponseSchema(
        name="GetExpirations",
        result_type=dict,
        description="Response from /public/get_expirations endpoint.",
        fields=[
            FieldSchema(
                name="eth",
                field_type=dict,
                required=False,
                nested_schema=[
                    FieldSchema(name="option", field_type=list, required=False),
                    FieldSchema(name="future", field_type=list, required=False),
                ]
            ),
            FieldSchema(
                name="btc",
                field_type=dict,
                required=False,
                nested_schema=[
                    FieldSchema(name="option", field_type=list, required=False),
                    FieldSchema(name="future", field_type=list, required=False),
                ]
            ),
        ]
    )

    INSTRUMENT = ResponseSchema(
        name="Instrument",
        result_type=list,
        description="Response from /public/get_instruments endpoint.",
        fields=[
            FieldSchema(name="instrument_name", field_type=str, required=True),
            FieldSchema(name="kind", field_type=str, required=True),
            FieldSchema(name="base_currency", field_type=str, required=True),
            FieldSchema(name="quote_currency", field_type=str, required=True),
            FieldSchema(name="is_active", field_type=bool, required=True),
            FieldSchema(name="settlement_period", field_type=str, required=False),
            FieldSchema(name="creation_timestamp", field_type=int, required=True),
            FieldSchema(name="expiration_timestamp", field_type=int, required=True),
            FieldSchema(name="strike", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="option_type", field_type=str, required=False, nullable=True),
            FieldSchema(name="min_trade_amount", field_type=[int, float], required=True),
            FieldSchema(name="tick_size", field_type=[int, float], required=True),
            FieldSchema(name="maker_commission", field_type=[int, float], required=True),
            FieldSchema(name="taker_commission", field_type=[int, float], required=True),
            FieldSchema(name="contract_size", field_type=[int, float], required=False),
            FieldSchema(name="instrument_id", field_type=int, required=False),
        ]
    )

    BOOK_SUMMARY = ResponseSchema(
        name="BookSummary",
        result_type=list,
        description="Response from /public/get_book_summary_by_currency endpoint.",
        fields=[
            FieldSchema(name="instrument_name", field_type=str, required=True),
            FieldSchema(name="base_currency", field_type=str, required=True),
            FieldSchema(name="quote_currency", field_type=str, required=True),
            FieldSchema(name="bid_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="ask_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="mid_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="mark_price", field_type=[int, float], required=True),
            FieldSchema(name="last", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="open_interest", field_type=[int, float], required=True),
            FieldSchema(name="volume", field_type=[int, float], required=True),
            FieldSchema(name="volume_usd", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="high", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="low", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="mark_iv", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="underlying_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="underlying_index", field_type=str, required=False, nullable=True),
            FieldSchema(name="creation_timestamp", field_type=int, required=True),
            FieldSchema(name="interest_rate", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="estimated_delivery_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="price_change", field_type=[int, float], required=False, nullable=True),
        ]
    )

    TICKER = ResponseSchema(
        name="Ticker",
        result_type=dict,
        description="Response from /public/ticker endpoint.",
        fields=[
            FieldSchema(name="instrument_name", field_type=str, required=True),
            FieldSchema(name="timestamp", field_type=int, required=True),
            FieldSchema(name="state", field_type=str, required=True),
            FieldSchema(name="last_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="mark_price", field_type=[int, float], required=True),
            FieldSchema(name="index_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="best_bid_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="best_ask_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="best_bid_amount", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="best_ask_amount", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="open_interest", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="min_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="max_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="settlement_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="estimated_delivery_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(
                name="stats",
                field_type=dict,
                required=True,
                nested_schema=[
                    FieldSchema(name="high", field_type=[int, float], required=False, nullable=True),
                    FieldSchema(name="low", field_type=[int, float], required=False, nullable=True),
                    FieldSchema(name="price_change", field_type=[int, float], required=False, nullable=True),
                    FieldSchema(name="volume", field_type=[int, float], required=False, nullable=True),
                    FieldSchema(name="volume_usd", field_type=[int, float], required=False, nullable=True),
                ]
            ),
            FieldSchema(name="current_funding", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="funding_8h", field_type=[int, float], required=False, nullable=True),
        ]
    )

    ORDER_BOOK = ResponseSchema(
        name="OrderBook",
        result_type=dict,
        description="Response from /public/get_order_book endpoint.",
        fields=[
            FieldSchema(name="instrument_name", field_type=str, required=True),
            FieldSchema(name="timestamp", field_type=int, required=True),
            FieldSchema(name="state", field_type=str, required=True),
            FieldSchema(name="bids", field_type=list, required=True),
            FieldSchema(name="asks", field_type=list, required=True),
            FieldSchema(name="last_price", field_type=[int, float], required=True),
            FieldSchema(name="mark_price", field_type=[int, float], required=True),
            FieldSchema(name="index_price", field_type=[int, float], required=True),
            FieldSchema(name="best_bid_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="best_ask_price", field_type=[int, float], required=False, nullable=True),
            FieldSchema(name="open_interest", field_type=[int, float], required=True),
        ]
    )

    FUNDING_CHART_DATA = ResponseSchema(
        name="FundingChartData",
        result_type=dict,
        description="Response from /public/get_funding_chart_data endpoint.",
        fields=[
            FieldSchema(
                name="data",
                field_type=list,
                required=True,
                item_schema=[
                    FieldSchema(name="timestamp", field_type=int, required=True),
                    FieldSchema(name="index_price", field_type=[int, float], required=True),
                    FieldSchema(name="interest_8h", field_type=[int, float], required=True),
                ]
            ),
            FieldSchema(name="interest_8h", field_type=[int, float], required=True),
            FieldSchema(name="current_interest", field_type=[int, float], required=True),
        ]
    )

    HISTORICAL_VOLATILITY = ResponseSchema(
        name="HistoricalVolatility",
        result_type=list,
        description="Response from /public/get_historical_volatility endpoint. Returns array of [timestamp, value] pairs.",
        fields=[]
    )

    VOLATILITY_INDEX_DATA = ResponseSchema(
        name="VolatilityIndexData",
        result_type=dict,
        description="Response from /public/get_volatility_index_data endpoint.",
        fields=[
            FieldSchema(name="data", field_type=list, required=True),
            FieldSchema(name="continuation", field_type=[str, type(None)], required=False, nullable=True),
        ]
    )

    TRADINGVIEW_CHART_DATA = ResponseSchema(
        name="TradingViewChartData",
        result_type=dict,
        description="Response from /public/get_tradingview_chart_data endpoint.",
        fields=[
            FieldSchema(name="ticks", field_type=list, required=True),
            FieldSchema(name="status", field_type=str, required=True),
        ]
    )

    LAST_TRADES_BY_CURRENCY = ResponseSchema(
        name="LastTradesByCurrency",
        result_type=dict,
        description="Response from /public/get_last_trades_by_currency endpoint.",
        fields=[
            FieldSchema(
                name="trades",
                field_type=list,
                required=True,
                item_schema=[
                    FieldSchema(name="trade_id", field_type=str, required=True),
                    FieldSchema(name="trade_seq", field_type=int, required=False),
                    FieldSchema(name="timestamp", field_type=int, required=True),
                    FieldSchema(name="instrument_name", field_type=str, required=True),
                    FieldSchema(name="price", field_type=[int, float], required=True),
                    FieldSchema(name="amount", field_type=[int, float], required=True),
                    FieldSchema(name="direction", field_type=str, required=True),
                    FieldSchema(name="iv", field_type=[int, float], required=False, nullable=True),
                    FieldSchema(name="mark_price", field_type=[int, float], required=False, nullable=True),
                    FieldSchema(name="index_price", field_type=[int, float], required=False, nullable=True),
                ]
            ),
            FieldSchema(name="has_more", field_type=bool, required=False),
        ]
    )

    INDEX_PRICE = ResponseSchema(
        name="IndexPrice",
        result_type=dict,
        description="Response from /public/get_index_price endpoint.",
        fields=[
            FieldSchema(name="index_price", field_type=[int, float], required=True),
            FieldSchema(name="estimated_delivery_price", field_type=[int, float], required=False, nullable=True),
        ]
    )

    @classmethod
    def get_schema_for_endpoint(cls, endpoint_path: str) -> ResponseSchema:
        """
        Get the appropriate schema for an endpoint path.

        Args:
            endpoint_path: The endpoint path (e.g., '/public/test').

        Returns:
            The corresponding ResponseSchema.

        Raises:
            ValueError: If no schema exists for the endpoint.
        """
        schema_map = {
            "/public/test": cls.TEST,
            "/public/get_expirations": cls.GET_EXPIRATIONS,
            "/public/get_instruments": cls.INSTRUMENT,
            "/public/get_book_summary_by_currency": cls.BOOK_SUMMARY,
            "/public/ticker": cls.TICKER,
            "/public/get_order_book": cls.ORDER_BOOK,
            "/public/get_funding_chart_data": cls.FUNDING_CHART_DATA,
            "/public/get_historical_volatility": cls.HISTORICAL_VOLATILITY,
            "/public/get_volatility_index_data": cls.VOLATILITY_INDEX_DATA,
            "/public/get_tradingview_chart_data": cls.TRADINGVIEW_CHART_DATA,
            "/public/get_last_trades_by_currency": cls.LAST_TRADES_BY_CURRENCY,
            "/public/get_last_trades_by_currency_and_time": cls.LAST_TRADES_BY_CURRENCY,  # Same schema
            "/public/get_index_price": cls.INDEX_PRICE,
        }

        if endpoint_path not in schema_map:
            raise ValueError(f"No schema defined for endpoint: {endpoint_path}")

        return schema_map[endpoint_path]
