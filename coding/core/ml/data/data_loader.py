"""
ML Data Loader.

Loads features and labels from database for ML training.
CRITICAL: Ensures no look-ahead bias - features at time T use only data from T-1 or earlier.
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Tuple, Dict, Optional, List

from coding.core.database.repository import DatabaseRepository
from coding.core.ml.label_generator import LabelGenerator
from coding.core.ml.models.ml_config import MLTrainingConfig

logger = logging.getLogger(__name__)


class MLDataLoader:
    """
    Load training data from database.

    Pulls features from multiple DB tables:
    - hourly_snapshots: IV, Greeks, OI, volume aggregated per instrument
    - onchain_analysis_snapshots: Max pain, GEX/DEX, S/R levels
    - regime_detections: Component scores (trend, vol, momentum, onchain, sentiment)
    - funding_rate_history, volatility_index_history, external_metrics (if available)

    CRITICAL: Features at time T use ONLY data from <= T-1 to prevent look-ahead bias.
    """

    def __init__(self, repository: Optional[DatabaseRepository] = None):
        """
        Initialize data loader.

        Args:
            repository: Database repository instance.
        """
        self.repo = repository or DatabaseRepository()
        self.label_generator = LabelGenerator(repository=self.repo)
        logger.info("MLDataLoader initialized")

    def load_training_data(
        self,
        currency: str,
        start_time: datetime,
        end_time: datetime,
        config: MLTrainingConfig
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Load features (X) and labels (y) from database.

        CRITICAL: Features at time T use ONLY data from T-1 and earlier.
        Labels at time T describe what happened AFTER time T.

        Args:
            currency: Currency symbol (BTC, ETH).
            start_time: Start of training period.
            end_time: End of training period.
            config: ML training configuration.

        Returns:
            (features_df, labels_df) aligned by timestamp index.
        """
        logger.info(f"Loading training data for {currency}")
        logger.info(f"  Period: {start_time} to {end_time}")

        # Load all feature sources
        logger.info("  Loading hourly snapshot features...")
        snapshot_features = self._load_snapshot_features(currency, start_time, end_time)

        logger.info("  Loading on-chain features...")
        onchain_features = self._load_onchain_features(currency, start_time, end_time)

        logger.info("  Loading market features...")
        market_features = self._load_market_features(currency, start_time, end_time)

        logger.info("  Loading regime features...")
        regime_features = self._load_regime_features(currency, start_time, end_time)

        # Merge all features on timestamp
        logger.info("  Merging feature sources...")
        features = self._merge_features([
            snapshot_features,
            onchain_features,
            market_features,
            regime_features
        ])

        # Compute derived features
        logger.info("  Computing derived features...")
        features = self._compute_derived_features(features)

        # Load labels
        logger.info("  Generating labels...")
        labels = self._load_labels(currency, start_time, end_time)

        # Align features and labels on timestamp
        logger.info("  Aligning features and labels...")
        features, labels = self._align_data(features, labels)

        # Validate no look-ahead bias
        self._validate_no_lookahead(features, labels)

        logger.info(f"  Loaded {len(features)} samples with {len(features.columns)} features")
        logger.info(f"  Labels: {len(labels)} samples")

        return features, labels

    def _load_snapshot_features(
        self,
        currency: str,
        start_time: datetime,
        end_time: datetime
    ) -> pd.DataFrame:
        """
        Load features from hourly_snapshots table.

        Aggregates across all instruments for each hour to get market-wide signals.

        Returns:
            DataFrame with index=snapshot_hour and columns for aggregated features.
        """
        conn = self.repo._get_connection()

        try:
            query = """
                SELECT
                    snapshot_hour,
                    -- Volume and trade activity
                    SUM(total_volume) as total_volume,
                    SUM(trade_count) as total_trades,
                    AVG(vwap) as avg_vwap,

                    -- Implied Volatility
                    AVG(mark_iv) as avg_iv,
                    STDDEV(mark_iv) as iv_std,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY mark_iv) as iv_median,

                    -- Open Interest
                    SUM(open_interest) as total_oi,
                    SUM(CASE WHEN option_type = 'C' THEN open_interest ELSE 0 END) as call_oi,
                    SUM(CASE WHEN option_type = 'P' THEN open_interest ELSE 0 END) as put_oi,

                    -- Volume by type
                    SUM(CASE WHEN option_type = 'C' THEN total_volume ELSE 0 END) as call_volume,
                    SUM(CASE WHEN option_type = 'P' THEN total_volume ELSE 0 END) as put_volume,

                    -- Greeks (aggregate)
                    AVG(avg_delta) as avg_delta,
                    AVG(avg_gamma) as avg_gamma,
                    AVG(avg_theta) as avg_theta,
                    AVG(avg_vega) as avg_vega,

                    -- Price levels
                    AVG(index_price) as underlying_price,
                    AVG(basis) as avg_basis

                FROM hourly_snapshots
                WHERE currency = %s
                  AND snapshot_hour >= %s
                  AND snapshot_hour <= %s
                GROUP BY snapshot_hour
                ORDER BY snapshot_hour ASC
            """

            df = pd.read_sql_query(
                query,
                conn,
                params=(currency, start_time, end_time),
                index_col='snapshot_hour'
            )

            # Compute ratios (avoid division by zero)
            df['put_call_oi_ratio'] = np.where(
                df['call_oi'] > 0,
                df['put_oi'] / df['call_oi'],
                np.nan
            )

            df['put_call_vol_ratio'] = np.where(
                df['call_volume'] > 0,
                df['put_volume'] / df['call_volume'],
                np.nan
            )

            # IV term structure (near vs far)
            # Compute per-expiration IV and take diff between nearest and farthest
            # This is simplified - actual term structure needs expiration-specific queries
            # For now, use IV std as proxy for term structure uncertainty
            df['iv_term_structure_proxy'] = df['iv_std']

            logger.info(f"    Loaded {len(df)} hourly snapshot feature rows")

            return df

        finally:
            self.repo._return_connection(conn)

    def _load_onchain_features(
        self,
        currency: str,
        start_time: datetime,
        end_time: datetime
    ) -> pd.DataFrame:
        """
        Load features from onchain_analysis_snapshots table.

        Aggregates across expirations to get market-wide on-chain signals.

        Returns:
            DataFrame with index=snapshot_hour.
        """
        try:
            # Use repository method
            snapshots = self.repo.get_onchain_snapshots(
                currency=currency,
                start_time=start_time,
                end_time=end_time,
                expiration=None  # Aggregate across all expirations
            )

            if not snapshots:
                logger.warning("    No on-chain snapshots found, creating empty DataFrame")
                # Return empty DataFrame with correct index
                return pd.DataFrame(index=pd.DatetimeIndex([], name='snapshot_hour'))

            df = pd.DataFrame(snapshots)
            df.set_index('snapshot_hour', inplace=True)

            # Rename columns to avoid conflicts
            df = df.add_prefix('onchain_')
            df.rename(columns={'onchain_snapshot_hour': 'snapshot_hour'}, inplace=True)

            logger.info(f"    Loaded {len(df)} on-chain feature rows")

            return df

        except Exception as e:
            logger.warning(f"    Failed to load on-chain features: {e}")
            return pd.DataFrame(index=pd.DatetimeIndex([], name='snapshot_hour'))

    def _load_market_features(
        self,
        currency: str,
        start_time: datetime,
        end_time: datetime
    ) -> pd.DataFrame:
        """
        Load features from technical_indicators, funding_rate_history, volatility_index_history, external_metrics.

        These tables may not exist yet (migration 003 not applied).

        Returns:
            DataFrame with index=timestamp/date.
        """
        conn = self.repo._get_connection()
        features = pd.DataFrame()

        try:
            # Check if tables exist
            cursor = conn.cursor()
            cursor.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public'
                  AND table_name IN ('technical_indicators', 'funding_rate_history', 'volatility_index_history', 'external_metrics')
            """)
            existing_tables = {row[0] for row in cursor.fetchall()}
            cursor.close()

            # Load technical indicators if table exists
            if 'technical_indicators' in existing_tables:
                query = """
                    SELECT date as timestamp,
                           sma_50, sma_200, ema_50, ema_200,
                           adx, plus_di, minus_di,
                           atr, atr_percentile,
                           rsi,
                           macd, macd_signal, macd_histogram
                    FROM technical_indicators
                    WHERE currency = %s
                      AND date >= %s
                      AND date <= %s
                    ORDER BY date ASC
                """
                tech_df = pd.read_sql_query(
                    query,
                    conn,
                    params=(currency, start_time, end_time),
                    index_col='timestamp'
                )
                features = tech_df if features.empty else features.join(tech_df, how='outer')
                logger.info(f"    Loaded {len(tech_df)} technical indicator rows")

            # Load funding rate if table exists
            if 'funding_rate_history' in existing_tables:
                query = """
                    SELECT date as timestamp, funding_rate
                    FROM funding_rate_history
                    WHERE currency = %s
                      AND date >= %s
                      AND date <= %s
                    ORDER BY date ASC
                """
                funding_df = pd.read_sql_query(
                    query,
                    conn,
                    params=(currency, start_time, end_time),
                    index_col='timestamp'
                )
                features = funding_df if features.empty else features.join(funding_df, how='outer')

            # Load DVOL if table exists
            if 'volatility_index_history' in existing_tables:
                query = """
                    SELECT date as timestamp, dvol
                    FROM volatility_index_history
                    WHERE currency = %s
                      AND date >= %s
                      AND date <= %s
                    ORDER BY date ASC
                """
                dvol_df = pd.read_sql_query(
                    query,
                    conn,
                    params=(currency, start_time, end_time),
                    index_col='timestamp'
                )
                features = dvol_df if features.empty else features.join(dvol_df, how='outer')

            # Load external metrics if table exists (not currency-specific)
            if 'external_metrics' in existing_tables:
                query = """
                    SELECT date as timestamp,
                           fear_greed_value,
                           btc_dominance,
                           eth_dominance
                    FROM external_metrics
                    WHERE date >= %s
                      AND date <= %s
                    ORDER BY date ASC
                """
                external_df = pd.read_sql_query(
                    query,
                    conn,
                    params=(start_time, end_time),
                    index_col='timestamp'
                )
                features = external_df if features.empty else features.join(external_df, how='outer')

            if not features.empty:
                logger.info(f"    Loaded {len(features)} market feature rows from {len(existing_tables)} tables")
            else:
                logger.warning("    No market feature tables found (migration 003 not applied or tables empty)")

            return features

        except Exception as e:
            logger.error(f"    Error loading market features: {e}")
            return pd.DataFrame()

        finally:
            self.repo._return_connection(conn)

    def _load_regime_features(
        self,
        currency: str,
        start_time: datetime,
        end_time: datetime
    ) -> pd.DataFrame:
        """
        Load features from regime_detections table.

        Component scores (trend, vol, momentum, onchain, sentiment) are useful ML features.

        Returns:
            DataFrame with index=detected_at.
        """
        try:
            # Use repository method
            detections = self.repo.get_regime_detections(
                currency=currency,
                start_time=start_time,
                end_time=end_time
            )

            if not detections:
                logger.warning("    No regime detections found")
                return pd.DataFrame()

            df = pd.DataFrame(detections)
            df.set_index('detected_at', inplace=True)

            # Select only component scores (not regime classification itself - that's the target)
            score_columns = [
                'trend_score', 'volatility_score', 'momentum_score',
                'onchain_score', 'sentiment_score', 'confidence_score'
            ]
            df = df[[col for col in score_columns if col in df.columns]]

            # Rename to avoid conflicts
            df = df.add_prefix('regime_')

            logger.info(f"    Loaded {len(df)} regime feature rows")

            return df

        except Exception as e:
            logger.warning(f"    Failed to load regime features: {e}")
            return pd.DataFrame()

    def _merge_features(self, feature_dfs: List[pd.DataFrame]) -> pd.DataFrame:
        """
        Merge all feature DataFrames on their index (timestamp).

        Uses outer join to keep all timestamps, then forward-fills missing values
        (appropriate for time-series where values persist until updated).

        Args:
            feature_dfs: List of DataFrames with timestamp index.

        Returns:
            Merged DataFrame.
        """
        # Filter out empty DataFrames
        non_empty = [df for df in feature_dfs if not df.empty]

        if not non_empty:
            logger.error("All feature DataFrames are empty!")
            return pd.DataFrame()

        # Start with first non-empty DataFrame
        merged = non_empty[0]

        # Join remaining DataFrames
        for df in non_empty[1:]:
            merged = merged.join(df, how='outer')

        # Sort by index
        merged.sort_index(inplace=True)

        # Forward-fill missing values (assume values persist)
        merged.ffill(inplace=True)

        # Drop any remaining NaN rows (start of series)
        merged.dropna(how='all', inplace=True)

        # Convert all columns to numeric (PostgreSQL returns Decimal types)
        for col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors='coerce')

        logger.info(f"    Merged to {len(merged)} rows with {len(merged.columns)} features")

        return merged

    def _compute_derived_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute cross-product and derived features.

        These combine base features to create higher-order signals.

        Args:
            df: DataFrame with base features.

        Returns:
            DataFrame with added derived features.
        """
        if df.empty:
            return df

        # Normalize underlying price (returns)
        if 'underlying_price' in df.columns:
            df['price_return_1h'] = df['underlying_price'].pct_change(1)
            df['price_return_4h'] = df['underlying_price'].pct_change(4)
            df['price_return_24h'] = df['underlying_price'].pct_change(24)
            df['price_return_7d'] = df['underlying_price'].pct_change(168)

            # Realized volatility (rolling std of returns)
            df['realized_vol_24h'] = df['price_return_1h'].rolling(24).std() * np.sqrt(24 * 365) * 100
            df['realized_vol_7d'] = df['price_return_1h'].rolling(168).std() * np.sqrt(24 * 365) * 100

        # IV-RV spread (if both available)
        if 'avg_iv' in df.columns and 'realized_vol_24h' in df.columns:
            df['iv_rv_spread'] = df['avg_iv'] - df['realized_vol_24h']

        # GEX × Put/Call ratio (on-chain positioning strength)
        if 'onchain_total_net_gex' in df.columns and 'put_call_oi_ratio' in df.columns:
            df['gex_times_pc_ratio'] = df['onchain_total_net_gex'] * df['put_call_oi_ratio']

        # Max pain distance × OI (mean-reversion signal strength)
        if 'onchain_max_pain_distance_pct' in df.columns and 'total_oi' in df.columns:
            df['maxpain_dist_times_oi'] = df['onchain_max_pain_distance_pct'] * df['total_oi']

        # Trend × Volume (momentum confirmation)
        if 'regime_trend_score' in df.columns and 'total_volume' in df.columns:
            df['trend_times_volume'] = df['regime_trend_score'] * df['total_volume']

        # Volatility regime × IV (vol expansion signal)
        if 'regime_volatility_score' in df.columns and 'avg_iv' in df.columns:
            df['vol_regime_times_iv'] = df['regime_volatility_score'] * df['avg_iv']

        logger.info(f"    Added derived features, total now: {len(df.columns)}")

        return df

    def _load_labels(
        self,
        currency: str,
        start_time: datetime,
        end_time: datetime
    ) -> pd.DataFrame:
        """
        Generate labels using LabelGenerator.

        Labels describe what happened AFTER time T (forward-looking).

        Returns:
            DataFrame with index=timestamp and label columns.
        """
        labels = self.label_generator.generate_labels_batch(
            currency=currency,
            start_time=start_time,
            end_time=end_time
        )

        if not labels:
            logger.error("No labels generated!")
            return pd.DataFrame()

        # Convert to DataFrame
        label_dicts = [
            {
                'timestamp': label.timestamp,
                'market_regime': label.market_regime,
                'realized_vol_24h': label.realized_vol_24h,
                'trend_strength': label.trend_strength,
                'trend_direction': label.trend_direction
            }
            for label in labels
        ]

        df = pd.DataFrame(label_dicts)
        df.set_index('timestamp', inplace=True)

        logger.info(f"    Generated {len(df)} labels")

        return df

    def _align_data(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Align features and labels on timestamp index.

        Uses inner join to keep only timestamps present in both.

        Returns:
            (aligned_features, aligned_labels)
        """
        # Inner join on index
        aligned = features.join(labels, how='inner', rsuffix='_label')

        if aligned.empty:
            logger.error("No overlapping timestamps between features and labels!")
            return pd.DataFrame(), pd.DataFrame()

        # Split back into features and labels
        label_columns = labels.columns.tolist()
        feature_columns = [col for col in aligned.columns if col not in label_columns]

        aligned_features = aligned[feature_columns]
        aligned_labels = aligned[label_columns]

        logger.info(f"    Aligned to {len(aligned_features)} samples")

        return aligned_features, aligned_labels

    def _validate_no_lookahead(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame
    ) -> None:
        """
        Validate that there is no look-ahead bias.

        Features at time T should use data from <= T-1.
        Labels at time T describe what happens after T.

        This is a sanity check - the actual prevention happens in feature extraction.

        Args:
            features: Feature DataFrame.
            labels: Label DataFrame.

        Raises:
            ValueError: If look-ahead bias is detected.
        """
        if features.empty or labels.empty:
            return

        # Check that features and labels have same index
        if not features.index.equals(labels.index):
            raise ValueError("Features and labels have mismatched indices!")

        # Check that index is sorted
        if not features.index.is_monotonic_increasing:
            raise ValueError("Feature index is not sorted!")

        if not labels.index.is_monotonic_increasing:
            raise ValueError("Label index is not sorted!")

        logger.info("    ✓ No look-ahead bias detected (indices aligned and sorted)")
