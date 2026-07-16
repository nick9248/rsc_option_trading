"""
Deribit API service for high-level operations.

Orchestrates connection, fetching, parsing, and validation of Deribit API data.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from coding.core.api.connection import ApiConnection
from coding.core.api.response_parser import ResponseParser
from coding.core.api.schema_validator import SchemaValidator
from coding.core.endpoints.deribit_endpoints import DeribitEndpoints, DERIBIT_BASE_URL
from coding.core.schemas.deribit_schemas import DeribitSchemas
from coding.pipelines.fetch_and_process import fetch_and_process


logger = logging.getLogger(__name__)


class DeribitApiService:
    """
    High-level service for interacting with the Deribit API.

    Provides methods to:
    1. Check API connectivity
    2. Fetch data from endpoints
    3. Parse responses
    4. Validate against expected schemas
    5. Save data to CSV
    """

    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3,
        validate_responses: bool = True,
        strict_validation: bool = False
    ):
        """
        Initialize the Deribit API service.

        Args:
            timeout: Request timeout in seconds.
            max_retries: Maximum retry attempts for failed requests.
            validate_responses: Whether to validate responses against schemas.
            strict_validation: If True, raise exceptions on validation failures.
        """
        self.connection = ApiConnection(
            base_url=DERIBIT_BASE_URL,
            timeout=timeout,
            max_retries=max_retries
        )
        self.parser = ResponseParser()
        self.validator = SchemaValidator(strict_mode=strict_validation)
        self.validate_responses = validate_responses

    def check_connectivity(self) -> Dict[str, Any]:
        """
        Test API connectivity and return server information.

        Returns:
            Dictionary with server version and connection status.

        Raises:
            ApiUnavailableError: If the API is not available.
        """
        logger.info("Checking Deribit API connectivity")

        self.connection.check_connectivity(DeribitEndpoints.TEST)
        response = self.connection.fetch(DeribitEndpoints.TEST)
        result = self.parser.extract_result(response)

        if self.validate_responses:
            self.validator.validate(result, DeribitSchemas.TEST)

        logger.info(f"Connected to Deribit API version: {result.get('version')}")
        return {
            "connected": True,
            "version": result.get("version"),
            "testnet": response.get("testnet", False)
        }

    def get_expirations(
        self,
        currency: str = "ETH",
        save_to_csv: bool = False
    ) -> Dict[str, Any]:
        """
        Get available expiration dates for a currency.

        Args:
            currency: Currency symbol (ETH, BTC).
            save_to_csv: Whether to save results to CSV.

        Returns:
            Dictionary with option and future expiration dates.
        """
        return fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_EXPIRATIONS,
            parameters={"currency": currency},
            validate_responses=self.validate_responses,
            save_to_csv=save_to_csv,
            csv_filename=f"expirations_{currency.lower()}"
        )

    def get_expirations_sorted_by_oi(
        self,
        currency: str = "ETH",
        include_oi: bool = False
    ) -> List[str]:
        """
        Get option expirations sorted by total open interest (descending).

        This method fetches all option expirations, calculates total OI for each,
        and returns them sorted with highest OI first.

        Args:
            currency: Currency symbol (ETH, BTC).
            include_oi: If True, returns "expiration (OI: 12345)" format. Default False.

        Returns:
            List of expiration strings sorted by OI descending.
        """
        # Get expirations from API
        expirations_data = self.get_expirations(currency=currency)

        # Extract option expirations
        currency_data = expirations_data.get(currency.lower(), {})
        option_expirations = currency_data.get("option", [])

        if not option_expirations:
            logger.warning(f"No option expirations found for {currency}")
            return []

        logger.info(f"Found {len(option_expirations)} option expirations for {currency}")

        # Get book summary to calculate OI
        book_summary = self.get_book_summary(currency=currency, kind="option")

        if not book_summary:
            logger.warning("Could not fetch book summary for OI sorting, using alphabetical order")
            return sorted(option_expirations)

        # Calculate total OI per expiration
        oi_by_expiration = {}
        for item in book_summary:
            instrument_name = item.get("instrument_name", "")
            parts = instrument_name.split("-")

            if len(parts) >= 4:
                expiration = parts[1]
                oi = item.get("open_interest", 0) or 0

                if expiration not in oi_by_expiration:
                    oi_by_expiration[expiration] = 0
                oi_by_expiration[expiration] += oi

        # Sort expirations by OI descending
        sorted_expirations = sorted(
            option_expirations,
            key=lambda exp: oi_by_expiration.get(exp, 0),
            reverse=True  # Highest OI first
        )

        # Format with OI if requested
        if include_oi:
            formatted_expirations = []
            for exp in sorted_expirations:
                oi = oi_by_expiration.get(exp, 0)
                # Format OI with commas for readability
                formatted_expirations.append(f"{exp} (OI: {oi:,.0f})")
            sorted_expirations = formatted_expirations

        logger.info(
            f"Sorted {len(sorted_expirations)} expirations by OI. "
            f"Top 3: {sorted_expirations[:3] if len(sorted_expirations) >= 3 else sorted_expirations}"
        )

        return sorted_expirations

    def get_instruments(
        self,
        currency: str = "ETH",
        kind: Optional[str] = "option",
        expired: bool = False,
        save_to_csv: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get available trading instruments for a currency.

        Args:
            currency: Currency symbol (ETH, BTC).
            kind: Instrument type filter (option, future, spot).
            expired: Include expired instruments.
            save_to_csv: Whether to save results to CSV.

        Returns:
            List of instrument details.
        """
        parameters = {"currency": currency}
        if kind:
            parameters["kind"] = kind
        if expired:
            parameters["expired"] = "true"

        return fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_INSTRUMENTS,
            parameters=parameters,
            validate_responses=self.validate_responses,
            save_to_csv=save_to_csv,
            csv_filename=f"instruments_{currency.lower()}_{kind or 'all'}",
            csv_subdirectory="instruments"
        )

    def get_book_summary(
        self,
        currency: str = "ETH",
        kind: Optional[str] = "option",
        save_to_csv: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get order book summary for all instruments of a currency.

        Args:
            currency: Currency symbol (ETH, BTC).
            kind: Instrument type filter (option, future, spot).
            save_to_csv: Whether to save results to CSV.

        Returns:
            List of book summary entries.
        """
        parameters = {"currency": currency}
        if kind:
            parameters["kind"] = kind

        return fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_BOOK_SUMMARY_BY_CURRENCY,
            parameters=parameters,
            validate_responses=self.validate_responses,
            save_to_csv=save_to_csv,
            csv_filename=f"book_summary_{currency.lower()}_{kind or 'all'}",
            csv_subdirectory="book_summary"
        )

    def get_last_trades_by_currency(
        self,
        currency: str = "ETH",
        kind: Optional[str] = "option",
        count: int = 1000,
        save_to_csv: bool = False
    ) -> Dict[str, Any]:
        """
        Get recent trades for all instruments of a currency.

        Args:
            currency: Currency symbol (ETH, BTC).
            kind: Instrument type filter (option, future, spot).
            count: Number of trades to retrieve (max 1000).
            save_to_csv: Whether to save results to CSV.

        Returns:
            Dictionary with trades data.
        """
        parameters = {"currency": currency, "count": count}
        if kind:
            parameters["kind"] = kind

        return fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_LAST_TRADES_BY_CURRENCY,
            parameters=parameters,
            validate_responses=self.validate_responses,
            save_to_csv=save_to_csv,
            csv_filename=f"last_trades_{currency.lower()}_{kind or 'all'}",
            csv_subdirectory="trades"
        )

    def get_last_trades_by_currency_and_time(
        self,
        currency: str = "ETH",
        start_timestamp: int = None,
        end_timestamp: int = None,
        kind: Optional[str] = "option",
        count: int = 1000,
        include_old: bool = True,
        save_to_csv: bool = False
    ) -> Dict[str, Any]:
        """
        Get historical trades for a currency within a specific time range.

        Args:
            currency: Currency symbol (ETH, BTC).
            start_timestamp: Start time in milliseconds (required).
            end_timestamp: End time in milliseconds (required).
            kind: Instrument type filter (option, future, spot).
            count: Number of trades to retrieve per request (max 1000).
            include_old: Include historical data.
            save_to_csv: Whether to save results to CSV.

        Returns:
            Dictionary with trades data including 'trades' list and 'has_more' flag.

        Raises:
            ValueError: If start_timestamp or end_timestamp not provided.
        """
        if start_timestamp is None or end_timestamp is None:
            raise ValueError("start_timestamp and end_timestamp are required")

        parameters = {
            "currency": currency,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "count": count,
            "include_old": include_old
        }
        if kind:
            parameters["kind"] = kind

        return fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_LAST_TRADES_BY_CURRENCY_AND_TIME,
            parameters=parameters,
            validate_responses=self.validate_responses,
            save_to_csv=save_to_csv,
            csv_filename=f"historical_trades_{currency.lower()}_{start_timestamp}_{end_timestamp}",
            csv_subdirectory="trades/historical"
        )

    def get_ticker(
        self,
        instrument_name: str,
        save_to_csv: bool = False
    ) -> Dict[str, Any]:
        """
        Get current ticker data for a specific instrument.

        Args:
            instrument_name: The instrument name (e.g., ETH-PERPETUAL).
            save_to_csv: Whether to save results to CSV.

        Returns:
            Ticker data dictionary.
        """
        return fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.TICKER,
            parameters={"instrument_name": instrument_name},
            validate_responses=self.validate_responses,
            save_to_csv=save_to_csv,
            csv_filename=f"ticker_{instrument_name.replace('-', '_').lower()}",
            csv_subdirectory="ticker"
        )

    def get_order_book(
        self,
        instrument_name: str,
        depth: int = 10,
        save_to_csv: bool = False
    ) -> Dict[str, Any]:
        """
        Get order book for a specific instrument.

        Args:
            instrument_name: The instrument name (e.g., ETH-PERPETUAL).
            depth: Number of price levels (1-10000).
            save_to_csv: Whether to save results to CSV.

        Returns:
            Order book data with bids and asks.
        """
        return fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_ORDER_BOOK,
            parameters={"instrument_name": instrument_name, "depth": depth},
            validate_responses=self.validate_responses,
            save_to_csv=save_to_csv,
            csv_filename=f"order_book_{instrument_name.replace('-', '_').lower()}",
            csv_subdirectory="order_book"
        )

    def get_funding_chart_data(
        self,
        instrument_name: str = "ETH-PERPETUAL",
        length: str = "8h",
        save_to_csv: bool = False
    ) -> Dict[str, Any]:
        """
        Get funding rate chart data for a perpetual instrument.

        Args:
            instrument_name: The perpetual instrument name.
            length: Time period (8h, 24h, 1m).
            save_to_csv: Whether to save results to CSV.

        Returns:
            Funding chart data with data points and summary.
        """
        return fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_FUNDING_CHART_DATA,
            parameters={"instrument_name": instrument_name, "length": length},
            validate_responses=self.validate_responses,
            save_to_csv=save_to_csv,
            csv_filename=f"funding_{instrument_name.replace('-', '_').lower()}_{length}",
            csv_subdirectory="funding"
        )

    def get_historical_volatility(
        self,
        currency: str = "ETH",
        save_to_csv: bool = False
    ) -> List[List]:
        """
        Get historical volatility data for a currency.

        Args:
            currency: Currency symbol (ETH, BTC).
            save_to_csv: Whether to save results to CSV.

        Returns:
            List of [timestamp, volatility] pairs.
        """
        result = fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_HISTORICAL_VOLATILITY,
            parameters={"currency": currency},
            validate_responses=self.validate_responses
        )

        if save_to_csv and isinstance(result, list):
            converted = self.parser.array_to_dicts(
                result,
                ["timestamp", "volatility"]
            )
            self.parser.to_csv(
                converted,
                f"historical_volatility_{currency.lower()}",
                "volatility"
            )

        return result

    def get_volatility_index_data(
        self,
        currency: str = "ETH",
        resolution: int = 3600,
        start_timestamp: int = None,
        end_timestamp: int = None,
        save_to_csv: bool = False
    ) -> Dict[str, Any]:
        """
        Get DVOL (volatility index) OHLC data.

        Args:
            currency: Currency symbol (ETH, BTC).
            resolution: Time resolution in seconds.
            start_timestamp: Start time in milliseconds.
            end_timestamp: End time in milliseconds.
            save_to_csv: Whether to save results to CSV.

        Returns:
            Dictionary with OHLC data array and continuation token.
        """
        if end_timestamp is None:
            end_timestamp = int(time.time() * 1000)
        if start_timestamp is None:
            start_timestamp = end_timestamp - (24 * 60 * 60 * 1000)

        result = fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_VOLATILITY_INDEX_DATA,
            parameters={
                "currency": currency,
                "resolution": resolution,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp
            },
            validate_responses=self.validate_responses
        )

        if save_to_csv and "data" in result:
            converted = self.parser.array_to_dicts(
                result["data"],
                ["timestamp", "open", "high", "low", "close"]
            )
            self.parser.to_csv(
                converted,
                f"volatility_index_{currency.lower()}",
                "volatility"
            )

        return result

    def get_tradingview_chart_data(
        self,
        instrument_name: str = "BTC-PERPETUAL",
        resolution: str = "1D",
        start_timestamp: int = None,
        end_timestamp: int = None,
        save_to_csv: bool = False
    ) -> Dict[str, Any]:
        """
        Get historical OHLCV data in TradingView format.

        Args:
            instrument_name: Instrument name (e.g., BTC-PERPETUAL, ETH-PERPETUAL).
            resolution: Time resolution (1, 3, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1D).
            start_timestamp: Start time in milliseconds.
            end_timestamp: End time in milliseconds.
            save_to_csv: Whether to save results to CSV.

        Returns:
            Dictionary with OHLCV data: {ticks: [...], status: 'ok'}
            Each tick: [timestamp, open, high, low, close, volume]
        """
        if end_timestamp is None:
            end_timestamp = int(time.time() * 1000)
        if start_timestamp is None:
            # Default to 200 days for moving average calculations
            start_timestamp = end_timestamp - (200 * 24 * 60 * 60 * 1000)

        result = fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_TRADINGVIEW_CHART_DATA,
            parameters={
                "instrument_name": instrument_name,
                "resolution": resolution,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp
            },
            validate_responses=self.validate_responses
        )

        if save_to_csv and "ticks" in result:
            converted = self.parser.array_to_dicts(
                result["ticks"],
                ["timestamp", "open", "high", "low", "close", "volume"]
            )
            self.parser.to_csv(
                converted,
                f"ohlcv_{instrument_name.replace('-', '_').lower()}",
                "ohlcv"
            )

        return result

    def get_index_price(
        self,
        currency: str = "BTC",
        save_to_csv: bool = False
    ) -> float:
        """
        Get the current Deribit index (spot) price for a currency.

        This is the price shown on Deribit's website and the price actually
        used to convert an option premium (quoted in the base currency, e.g.
        BTC) into USD. It is NOT the same as the per-expiry future price
        (`underlying_price` in get_book_summary/ticker responses), which
        trades above/below the index by the futures basis. See
        get_option_chain_snapshot for the authoritative explanation of why
        this distinction matters.

        Args:
            currency: Currency symbol (BTC, ETH, ...).
            save_to_csv: Whether to save the result to CSV.

        Returns:
            Current index price as a float.
        """
        index_name = f"{currency.lower()}_usd"

        result = fetch_and_process(
            connection=self.connection,
            parser=self.parser,
            validator=self.validator,
            endpoint=DeribitEndpoints.GET_INDEX_PRICE,
            parameters={"index_name": index_name},
            validate_responses=self.validate_responses,
            save_to_csv=save_to_csv,
            csv_filename=f"index_price_{currency.lower()}",
            csv_subdirectory="index_price"
        )

        return float(result.get("index_price"))

    def get_option_chain_snapshot(self, currency: str) -> Dict[str, Any]:
        """
        Get a single, authoritative, correctly-priced snapshot of an entire
        options chain for a currency. This is the ONE method every current
        and future consumer should call for option-chain market data — it
        exists to stop the index-vs-future pricing bug from recurring.

        THE INDEX-VS-FUTURE RULE (read this before touching pricing code):
        ---------------------------------------------------------------
        Deribit quotes option premiums in the base currency (e.g. BTC). To
        get the USD value of a premium, Deribit's own website multiplies by
        the INDEX price (a currency's spot index, e.g. "btc_usd") — that is
        what a user actually pays/receives in USD terms:

            premium_usd = premium_btc * index_price

        Each option's response also carries `underlying_price`, which is
        NOT the index — it is the price of the FUTURE contract expiring on
        that option's own expiry date. Futures trade above/below the index
        by the "basis" (currently roughly +0.3% to +0.8% for BTC, positive
        = contango), and the basis differs per expiry. Using
        `underlying_price` to convert a premium to USD is WRONG and was a
        confirmed pricing bug (BTC-28AUG26-64000-C: ask x index = $3,159,
        matching Deribit's website, vs ask x underlying_price = $3,173).

        `underlying_price` (the future) is still the CORRECT basis for:
          - strike distance / moneyness (both live in settlement space)
          - breakeven and expected-range math (F*exp(+-sigma*sqrt(T)))
          - anything comparing a strike against where the contract settles

        Never use one where the other belongs. This method returns both,
        clearly separated, so callers cannot conflate them by accident.

        Args:
            currency: Currency symbol (BTC, ETH, ...).

        Returns:
            Dict with:
                as_of: UTC datetime of this fetch.
                index_price: Current spot index price (USD per unit currency).
                contracts: List of per-instrument dicts, each with:
                    instrument_name, expiry (str, e.g. "25SEP26"),
                    expiry_datetime (UTC datetime, 08:00 UTC settlement),
                    dte (float days, NOT truncated), strike, option_type
                    ("C"/"P"), bid_price/ask_price/mark_price (RAW, in the
                    base currency e.g. BTC — never dropped), mark_iv
                    (Deribit native units, i.e. percent, e.g. 65.0 = 65%),
                    open_interest, volume, underlying_price (this expiry's
                    future price), bid_usd/ask_usd/mark_usd (= raw price *
                    index_price, or None if the raw price or index_price
                    is unavailable).
                futures_by_expiry: Dict of expiry (str) -> underlying_price
                    (float), one future price per expiry. Picked from the
                    highest-volume contract within that expiry to avoid the
                    stale cached underlying_price that illiquid strikes can
                    carry (mirrors the pattern already used in
                    SnapshotService._extract_underlying_price).

        Raises:
            ApiConnectionError / ResponseParseError: propagated from the
                underlying fetch calls if the API is unreachable or the
                response is malformed.
        """
        from datetime import datetime, timezone

        as_of = datetime.now(timezone.utc)
        index_price = self.get_index_price(currency=currency)
        raw_contracts = self.get_book_summary(currency=currency, kind="option")

        contracts: List[Dict[str, Any]] = []
        by_expiry: Dict[str, List[Dict[str, Any]]] = {}

        for item in raw_contracts:
            name = item.get("instrument_name", "")
            parts = name.split("-")
            if len(parts) < 4:
                logger.warning(f"Skipping malformed instrument name: {name}")
                continue

            try:
                strike = float(parts[2])
                option_type = parts[3][0].upper()  # "C" or "P"
                expiry_str = parts[1]  # e.g. "25SEP26"
                # Deribit options settle at 08:00 UTC.
                expiry_datetime = datetime.strptime(expiry_str, "%d%b%y").replace(
                    hour=8, minute=0, second=0, tzinfo=timezone.utc
                )
            except (ValueError, IndexError) as error:
                logger.warning(f"Skipping unparseable instrument {name}: {error}")
                continue

            dte = (expiry_datetime - as_of).total_seconds() / 86400.0

            bid_price = item.get("bid_price")
            ask_price = item.get("ask_price")
            mark_price = item.get("mark_price")
            underlying_price = item.get("underlying_price")

            bid_usd = bid_price * index_price if bid_price is not None and index_price else None
            ask_usd = ask_price * index_price if ask_price is not None and index_price else None
            mark_usd = mark_price * index_price if mark_price is not None and index_price else None

            contract = {
                "instrument_name": name,
                "expiry": expiry_str,
                "expiry_datetime": expiry_datetime,
                "dte": dte,
                "strike": strike,
                "option_type": option_type,
                "bid_price": bid_price,
                "ask_price": ask_price,
                "mark_price": mark_price,
                "mark_iv": item.get("mark_iv"),
                "open_interest": item.get("open_interest"),
                "volume": item.get("volume"),
                "underlying_price": underlying_price,
                "bid_usd": bid_usd,
                "ask_usd": ask_usd,
                "mark_usd": mark_usd,
            }
            contracts.append(contract)
            by_expiry.setdefault(expiry_str, []).append(item)

        futures_by_expiry: Dict[str, float] = {}
        for expiry_str, items in by_expiry.items():
            active = [i for i in items if (i.get("volume") or 0) > 0 and i.get("underlying_price")]
            if active:
                futures_by_expiry[expiry_str] = max(
                    active, key=lambda i: i.get("volume", 0)
                ).get("underlying_price")
            else:
                priced = [i for i in items if i.get("underlying_price")]
                futures_by_expiry[expiry_str] = priced[0]["underlying_price"] if priced else None

        return {
            "as_of": as_of,
            "index_price": index_price,
            "contracts": contracts,
            "futures_by_expiry": futures_by_expiry,
        }

    def close(self) -> None:
        """Close the API connection."""
        self.connection.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False
