"""Test OHLCV data format from Deribit API."""

import logging
from coding.core.logging.logging_setup import init_logging
from coding.service.deribit.deribit_api_service import DeribitApiService

# Initialize logging
init_logging(level="INFO")
logger = logging.getLogger(__name__)

def main():
    """Test OHLCV data format."""
    with DeribitApiService() as api_service:
        result = api_service.get_tradingview_chart_data(
            instrument_name="BTC-PERPETUAL",
            resolution="1D"
        )

        print("Result keys:", result.keys())
        print("Status:", result.get("status"))
        print("Number of ticks:", len(result.get("ticks", [])))
        print("\nFirst 3 ticks:")
        for i, tick in enumerate(result.get("ticks", [])[:3]):
            print(f"  Tick {i}: {tick}")
            print(f"    Type: {type(tick)}")
            if isinstance(tick, list):
                print(f"    Length: {len(tick)}")
                print(f"    Values: {tick}")

if __name__ == "__main__":
    main()
