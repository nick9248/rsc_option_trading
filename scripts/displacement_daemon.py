#!/usr/bin/env python3
"""
Displacement scanner daemon — runs 24/7 on VPS.

Deployed as a systemd service. Scans BTC and ETH every 5 minutes.
Sends Telegram alerts when a displacement event is detected with
sufficient conviction.

Local test:
    python scripts/displacement_daemon.py

VPS deployment (after sync):
    systemctl start displacement-scanner
"""
import logging
import time

from coding.core.logging.logging_setup import init_logging
from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.displacement.displacement_scanner_service import DisplacementScannerService

SCAN_INTERVAL_SECONDS = 5 * 60  # 5 minutes

logger = logging.getLogger(__name__)


def main() -> None:
    init_logging(level="INFO")
    logger.info("Displacement daemon starting")

    config = DisplacementConfig()
    api = DeribitApiService()
    repo = DatabaseRepository()
    scanner = DisplacementScannerService(config=config, api_service=api, repository=repo)

    logger.info("Scanning BTC and ETH every %d minutes", SCAN_INTERVAL_SECONDS // 60)

    while True:
        try:
            signals = scanner.scan(["BTC", "ETH"])
            if signals:
                for sig in signals:
                    logger.info(
                        "Alert fired: %s %.0f%% conviction (%s) — %s",
                        sig.asset,
                        sig.conviction_pct,
                        sig.conviction_label,
                        sig.instrument_name or "no contract",
                    )
            else:
                logger.debug("Scan complete — no displacement detected")
        except Exception as e:
            logger.error("Scan loop error: %s", e, exc_info=True)

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
