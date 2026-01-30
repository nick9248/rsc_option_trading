"""
External API integration for market sentiment and macro metrics.

Integrates free APIs:
- Alternative.me Fear & Greed Index
- CoinGecko Global Market Data (BTC Dominance)
"""

import logging
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class FearGreedAPI:
    """
    Fetch data from Alternative.me Fear & Greed Index API.

    Free API, no authentication required.
    Rate limit: None specified (reasonable use recommended)
    """

    BASE_URL = "https://api.alternative.me"

    def __init__(self, timeout: int = 10):
        """
        Initialize Fear & Greed API client.

        Args:
            timeout: Request timeout in seconds.
        """
        self.timeout = timeout
        logger.info("Initialized FearGreedAPI")

    def get_latest(self) -> Optional[Dict]:
        """
        Get the latest Fear & Greed Index value.

        Returns:
            Dictionary with index data or None if request fails.

        Example response:
            {
                "name": "Fear and Greed Index",
                "data": [{
                    "value": "45",
                    "value_classification": "Fear",
                    "timestamp": "1640000000",
                    "time_until_update": "43200"
                }],
                "metadata": {...}
            }
        """
        try:
            url = f"{self.BASE_URL}/fng/"
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()

            data = response.json()

            if "data" in data and len(data["data"]) > 0:
                latest = data["data"][0]
                result = {
                    "value": int(latest["value"]),
                    "classification": latest["value_classification"],
                    "timestamp": int(latest["timestamp"]),
                }
                logger.info(
                    f"Fear & Greed Index: {result['value']} ({result['classification']})"
                )
                return result

            logger.warning("No data in Fear & Greed API response")
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch Fear & Greed Index: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing Fear & Greed Index: {e}", exc_info=True)
            return None

    def get_historical(self, limit: int = 30) -> Optional[List[Dict]]:
        """
        Get historical Fear & Greed Index values.

        Args:
            limit: Number of historical data points (1-365).

        Returns:
            List of index values or None if request fails.
        """
        try:
            url = f"{self.BASE_URL}/fng/?limit={limit}"
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()

            data = response.json()

            if "data" in data:
                results = []
                for item in data["data"]:
                    results.append({
                        "value": int(item["value"]),
                        "classification": item["value_classification"],
                        "timestamp": int(item["timestamp"]),
                    })

                logger.info(f"Fetched {len(results)} historical Fear & Greed values")
                return results

            logger.warning("No data in Fear & Greed API response")
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch historical Fear & Greed Index: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing historical Fear & Greed Index: {e}", exc_info=True)
            return None


class CoinGeckoAPI:
    """
    Fetch global market data from CoinGecko API.

    Free API, no authentication required.
    Rate limit: 10-30 requests/minute (public endpoints)
    """

    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self, timeout: int = 10):
        """
        Initialize CoinGecko API client.

        Args:
            timeout: Request timeout in seconds.
        """
        self.timeout = timeout
        logger.info("Initialized CoinGeckoAPI")

    def get_global_market_data(self) -> Optional[Dict]:
        """
        Get global cryptocurrency market data including BTC dominance.

        Returns:
            Dictionary with market data or None if request fails.

        Example response:
            {
                "data": {
                    "active_cryptocurrencies": 10000,
                    "markets": 800,
                    "total_market_cap": {...},
                    "total_volume": {...},
                    "market_cap_percentage": {
                        "btc": 45.5,
                        "eth": 18.2,
                        ...
                    },
                    "market_cap_change_percentage_24h_usd": 2.5,
                    "updated_at": 1640000000
                }
            }
        """
        try:
            url = f"{self.BASE_URL}/global"
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()

            data = response.json()

            if "data" in data:
                market_data = data["data"]
                result = {
                    "btc_dominance": market_data["market_cap_percentage"].get("btc"),
                    "eth_dominance": market_data["market_cap_percentage"].get("eth"),
                    "total_market_cap_usd": market_data["total_market_cap"].get("usd"),
                    "total_volume_usd": market_data["total_volume"].get("usd"),
                    "market_cap_change_24h": market_data.get("market_cap_change_percentage_24h_usd"),
                    "active_cryptocurrencies": market_data.get("active_cryptocurrencies"),
                    "updated_at": market_data.get("updated_at"),
                }

                # Safe formatting with None checks
                btc_dom_str = f"{result['btc_dominance']:.2f}%" if result['btc_dominance'] is not None else "N/A"
                eth_dom_str = f"{result['eth_dominance']:.2f}%" if result['eth_dominance'] is not None else "N/A"
                logger.info(
                    f"BTC Dominance: {btc_dom_str}, "
                    f"ETH Dominance: {eth_dom_str}"
                )
                return result

            logger.warning("No data in CoinGecko global API response")
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch CoinGecko global data: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing CoinGecko global data: {e}", exc_info=True)
            return None

    def get_btc_dominance(self) -> Optional[float]:
        """
        Get Bitcoin market cap dominance percentage.

        Returns:
            BTC dominance as percentage (e.g., 45.5 for 45.5%) or None if request fails.
        """
        data = self.get_global_market_data()
        if data:
            return data.get("btc_dominance")
        return None

    def get_eth_dominance(self) -> Optional[float]:
        """
        Get Ethereum market cap dominance percentage.

        Returns:
            ETH dominance as percentage or None if request fails.
        """
        data = self.get_global_market_data()
        if data:
            return data.get("eth_dominance")
        return None


class ExternalMetricsFetcher:
    """
    Unified interface for fetching external market metrics.

    Combines multiple free APIs into a single service.
    """

    def __init__(self, timeout: int = 10):
        """
        Initialize all external API clients.

        Args:
            timeout: Request timeout for all APIs.
        """
        self.fear_greed = FearGreedAPI(timeout=timeout)
        self.coingecko = CoinGeckoAPI(timeout=timeout)
        logger.info("Initialized ExternalMetricsFetcher")

    def fetch_all_metrics(self) -> Dict:
        """
        Fetch all available external metrics.

        Returns:
            Dictionary with all metric data.

        Example:
            {
                "fear_greed": {
                    "value": 45,
                    "classification": "Fear",
                    "timestamp": 1640000000
                },
                "btc_dominance": 45.5,
                "eth_dominance": 18.2,
                "market_cap_change_24h": 2.5,
                "timestamp": 1640000000
            }
        """
        import time

        metrics = {
            "timestamp": int(time.time()),
            "fear_greed": None,
            "btc_dominance": None,
            "eth_dominance": None,
            "market_cap_change_24h": None,
        }

        # Fetch Fear & Greed Index
        fear_greed_data = self.fear_greed.get_latest()
        if fear_greed_data:
            metrics["fear_greed"] = fear_greed_data

        # Fetch CoinGecko global data
        global_data = self.coingecko.get_global_market_data()
        if global_data:
            metrics["btc_dominance"] = global_data.get("btc_dominance")
            metrics["eth_dominance"] = global_data.get("eth_dominance")
            metrics["market_cap_change_24h"] = global_data.get("market_cap_change_24h")

        # Safe formatting with None checks
        fg_str = metrics['fear_greed']['value'] if metrics['fear_greed'] else 'N/A'
        btc_dom_str = f"{metrics['btc_dominance']:.2f}%" if metrics['btc_dominance'] is not None else 'N/A'
        logger.info(
            f"Fetched external metrics: "
            f"F&G={fg_str}, "
            f"BTC Dom={btc_dom_str}"
        )

        return metrics
