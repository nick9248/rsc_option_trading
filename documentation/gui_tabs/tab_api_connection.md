# API Connection Tab

## Overview

A test console for the public Deribit API (and two external market-data APIs). You pick an endpoint, fill in its parameters, press Run, and read the response in the log area. It is meant for checking connectivity and inspecting raw responses.

## Features

- Endpoint dropdown. Deribit: Test Connection, Get Expirations, Get Instruments, Get Book Summary, Get Ticker, Get Order Book, Get Funding Chart, Get Historical Volatility, Get Volatility Index, Get Last Trades, Get Last Trades By Time, Get TradingView Chart. External: Fear & Greed Index, CoinGecko Market Data.
- Parameter inputs that change with the chosen endpoint (currency, kind, instrument name, time range, ...). Instrument fields have a "Load" button that fetches the instrument list.
- **Run** and **Clear Logs** buttons, a status label (Running / Success / Error), and an output log with the response.
- API calls run in a background thread, so the window stays responsive.

## Architecture

```
ApiConnectionTab (coding/gui/tabs/api_connection_tab.py)
    -> ApiTestService.invoke(endpoint_name, parameters)   (coding/service/api_testing/api_test_service.py)
        -> DeribitApiService / external client
```

The endpoint definitions are in `coding/core/endpoints/deribit_endpoints.py`.

## Notes

- Only public endpoints are used here. The authenticated, read-only account client (`DeribitPrivateClient`, `coding/service/deribit/deribit_private_client.py`) is used by the data collection daemon, not by this tab.
- Tests: `tests/unit/gui/test_api_connection_tab.py`, `tests/unit/test_api_test_service.py`, `tests/unit/test_api_connectivity_check.py`.
