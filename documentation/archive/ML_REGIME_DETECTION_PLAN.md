# ML-Powered Market Regime Detection & Prediction System

## Executive Summary

Design and implement an institutional-grade deep learning system for cryptocurrency market regime detection and prediction, leveraging Intel 14900K CPU and NVIDIA RTX 5090 GPU for maximum performance. The system will replace/augment the current rule-based regime detector with a self-improving neural architecture that learns from historical data and adapts over time.

**CRITICAL UPDATE - DATA BACKFILL SOLUTION FOUND**: After exploring all available data sources:
- `deribit_options_data` database: Only **34 days of ETH options data** (1-2 daily snapshots) - INSUFFICIENT
- `historical_data/` folder: **5 years of BTC/ETH spot price data** (daily OHLCV) - useful for features but lacks options data
- **Deribit Public API**: **FREE historical trades for all BTC/ETH options since 2018!** ✅

**GAME CHANGER**: We can backfill **6-12 months of historical options data for FREE** using Deribit Public API in ~1 week. This eliminates the 3-6 month Phase 0 waiting period. Greeks can be calculated from the provided IV using Black-Scholes.

**New Timeline**: **2-3 months total** (1 week backfill + 8 weeks ML development) instead of 5-8 months!

---

## Current State Analysis

### Existing Rule-Based System
- **5-component weighted scoring**: Trend (30%), Volatility (10%), Momentum (25%), On-Chain (20%), Sentiment (15%)
- **Output**: 5 regime classes (Strong Bullish, Weak Bullish, Sideways, Weak Bearish, Strong Bearish)
- **Limitations**: Fixed weights, linear relationships, no temporal dependencies, no probabilistic confidence

### Available Data Infrastructure
- **70+ features** across technical indicators, on-chain metrics, sentiment data
- **Time-series tables**: OHLCV, technical indicators, funding rates, DVOL, GEX/DEX, external metrics
- **Historical captures**: Max pain, OI, volume, support/resistance levels
- **Storage**: PostgreSQL with indexed time-series tables

### Existing Historical Data (deribit_options_data Database)
**ASSESSMENT: INSUFFICIENT FOR PRODUCTION ML TRAINING**

- **option_snapshots**: 79,344 rows (Oct 31 - Dec 4, 2025 = 34 days)
  - Currency: ETH only (NO BTC)
  - Frequency: 1-2 snapshots/day (too sparse)
  - Greeks completeness: 85.3%
  - Mark IV: 100%
  - 119 strikes, 46 expirations

- **gex_dex_snapshots**: 1,076 rows (Oct 31 - Nov 28, 2025 = 27 days)
  - Frequency: 40+ snapshots/day (better)
  - Market regime, gamma/delta exposure

- **orderbook_snapshots**: 633 rows (Nov 13-24, 2025 = 10 days only)
  - Too sparse to be useful

**Critical Gaps:**
- ❌ **Duration**: 34 days << 90-180 days minimum
- ❌ **Currency**: ETH only (need BTC + ETH)
- ❌ **Frequency**: 1-2 daily << hourly minimum
- ❌ **Labels**: No trade execution outcomes (P&L, expiration results)
- ❌ **Continuity**: Sporadic captures, not continuous time-series

**Conclusion**: Current data can be used for **prototyping and architecture testing only**. Production ML training blocked until 3-6 months of continuous hourly data is collected.

### Key Gaps
- **BLOCKING**: Insufficient historical training data (34 days vs 3-6 months needed)
- No automated continuous data collection (must implement)
- No ML infrastructure (model storage, training pipelines)
- No trade execution labels (need supervised learning targets)
- No BTC coverage (ETH-only limits model applicability)

---

## Proposed ML Architecture: Hybrid Multi-Modal Temporal Fusion Network

### Design Philosophy
**Institutional-grade**: Self-improving, interpretable, robust to market changes, production-ready

**Key Innovations**:
1. **Multi-modal fusion**: Separate encoders for technical, on-chain, and sentiment data
2. **Temporal attention**: Capture regime transitions and persistence patterns
3. **Probabilistic output**: Distribution over regimes (not hard classification)
4. **Continual learning**: Online adaptation as new data arrives
5. **Interpretability**: Attention weights show which features drove predictions

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      INPUT FEATURE STREAMS                       │
├──────────────────┬──────────────────┬─────────────────┬─────────┤
│   Technical      │    On-Chain      │   Sentiment     │  Market │
│   Indicators     │    Metrics       │   Signals       │  Context│
│ (SMA, RSI, ADX)  │ (GEX, DEX, OI)   │(Fear/Greed, FR) │  (Vol)  │
└────────┬─────────┴────────┬─────────┴────────┬────────┴────┬────┘
         │                  │                  │             │
         ▼                  ▼                  ▼             ▼
    ┌────────┐        ┌────────┐        ┌────────┐    ┌────────┐
    │ Tech   │        │OnChain │        │Sentiment│    │Market  │
    │Encoder │        │Encoder │        │Encoder  │    │Encoder │
    │(1D CNN)│        │(Dense) │        │(Dense)  │    │(Dense) │
    └───┬────┘        └───┬────┘        └───┬─────┘    └───┬────┘
        │                 │                  │              │
        └─────────────────┴──────────────────┴──────────────┘
                              │
                              ▼
                   ┌──────────────────────┐
                   │  Feature Fusion      │
                   │  (Cross-Attention)   │
                   └──────────┬───────────┘
                              │
                              ▼
                   ┌──────────────────────┐
                   │  Temporal Modeling   │
                   │  (Bi-LSTM + Attn)    │
                   │  or Transformer      │
                   └──────────┬───────────┘
                              │
              ┌───────────────┴────────────────┐
              │                                │
              ▼                                ▼
    ┌─────────────────┐              ┌─────────────────┐
    │ Detection Head  │              │ Prediction Head │
    │ (Current Regime)│              │(Future Regime)  │
    │ Softmax(5)      │              │Softmax(5) @ t+N │
    └─────────────────┘              └─────────────────┘
              │                                │
              ▼                                ▼
       Regime Probs                    Future Regime Probs
       + Confidence                    (1h, 4h, 24h ahead)
```

---

## Detailed Component Design

### 1. Multi-Modal Encoders

#### Technical Indicator Encoder (1D CNN)
**Input**: Time-series of technical indicators (lookback: 50 timesteps)
- Features: SMA50/200, EMA50/200, RSI, MACD, ADX, ATR percentile
- Architecture:
  - Conv1D(64 filters, kernel=3) → BatchNorm → ReLU → MaxPool
  - Conv1D(128 filters, kernel=3) → BatchNorm → ReLU → MaxPool
  - Conv1D(256 filters, kernel=3) → BatchNorm → ReLU
  - Global Average Pooling → Dense(128)
- **Why CNN**: Captures local patterns (e.g., golden cross, MACD crossover)
- **Output**: 128-dim embedding

#### On-Chain Metrics Encoder (Dense Network)
**Input**: Current snapshot + 10-timestep history
- Features: GEX (total, call wall, put wall), DEX, max pain distance, put/call ratio, OI distribution
- Architecture:
  - Dense(256) → LayerNorm → ReLU → Dropout(0.3)
  - Dense(128) → LayerNorm → ReLU → Dropout(0.2)
  - Dense(128)
- **Why Dense**: On-chain metrics are aggregates (not sequential patterns)
- **Output**: 128-dim embedding

#### Sentiment Encoder (Dense Network)
**Input**: External sentiment signals
- Features: Fear & Greed Index, funding rate, BTC dominance, ETH dominance
- Architecture:
  - Dense(64) → LayerNorm → ReLU
  - Dense(128)
- **Output**: 128-dim embedding

#### Market Context Encoder (Dense Network)
**Input**: Macro market state
- Features: Volatility regime, trend strength, current price, 24h volume
- Architecture:
  - Dense(64) → LayerNorm → ReLU
  - Dense(128)
- **Output**: 128-dim embedding

### 2. Cross-Modal Fusion Layer

**Multi-Head Cross-Attention** (4 heads, 128-dim per head)
- Allows model to learn which modalities are most relevant for current market state
- Query: Technical embedding
- Keys/Values: On-chain, Sentiment, Market embeddings
- Output: 512-dim fused representation (concat of 4 × 128)

**Why Cross-Attention**:
- Technical indicators might dominate during trending markets
- On-chain metrics might dominate during consolidation
- Model learns context-dependent weighting

### 3. Temporal Modeling Layer

**Option A: Causal (Unidirectional) LSTM + Attention** (REQUIRED for live inference - NOT Bi-LSTM!)
- **Unidirectional LSTM** (256 hidden units, 2 layers) - processes ONLY past context
- Self-attention over LSTM outputs (captures regime persistence)
- Output: 512-dim temporal embedding
- **CRITICAL**: Must be causal (no future information) for production deployment

**Option B: Causal Transformer Encoder** (For scaling to longer sequences)
- 4 encoder layers with **causal masking** (strict left-to-right attention only)
- 8 attention heads
- 512-dim model dimension
- Positional encoding for time awareness
- **CRITICAL**: Masking prevents look-ahead bias

**❌ REMOVED: Bi-Directional LSTM** - Original plan suggested Bi-LSTM which uses FUTURE context. This causes look-ahead bias: backtest appears accurate but live performance fails catastrophically.

**Why Temporal Modeling**:
- Regimes persist over time (Markov property)
- Regime transitions have characteristic patterns
- Historical context improves classification

### 4. Dual Output Heads

#### Detection Head (Current Regime)
- Dense(256) → ReLU → Dropout(0.3)
- Dense(128) → ReLU
- **Softmax(5)** → Probability distribution over 5 regimes
- **Auxiliary output**: Confidence score (max probability)

#### Prediction Head (Future Regime)
- Dense(256) → ReLU → Dropout(0.3)
- Dense(128) → ReLU
- **3 parallel branches**:
  - Softmax(5) for 1-hour ahead
  - Softmax(5) for 4-hour ahead
  - Softmax(5) for 24-hour ahead
- **Why multiple horizons**: Different strategies need different time horizons

### 5. Loss Function Design

**Multi-Task Loss** (weighted combination):

1. **Detection Loss** (40% weight):
   - Focal Loss for current regime classification
   - Handles class imbalance (sideways markets are more common)
   - Formula: FL = -α(1-p)^γ log(p), where γ=2

2. **Prediction Loss** (30% weight):
   - Cross-entropy for future regime prediction
   - Separate losses for 1h/4h/24h horizons

3. **Confidence Calibration Loss** (10% weight):
   - Encourage high confidence when correct, low when uncertain
   - Brier score for probabilistic calibration

4. **Temporal Consistency Loss** (10% weight):
   - Penalize rapid regime switches (smoothness prior)
   - L2 penalty on consecutive regime predictions

5. **Interpretability Loss** (10% weight):
   - Attention entropy regularization
   - Encourage sparse, interpretable attention weights

**Total Loss**: L_total = 0.4×L_detect + 0.3×L_predict + 0.1×L_calib + 0.1×L_temp + 0.1×L_interp

---

## Training Strategy

### Phase 1: Historical Data Collection & Labeling

#### Data Requirements
- **Minimum**: 3 months of continuous data (90 days × 24h = 2,160 hourly samples)
- **Optimal**: 6-12 months (4,320-8,760 samples)
- **Backfill strategy**:
  1. Use Deribit public API `/public/get_index_price_history` for OHLCV
  2. Fetch historical funding rates
  3. Fetch Fear & Greed Index from Alternative.me historical API
  4. Calculate technical indicators from OHLCV
  5. Store in database with proper timestamps

#### Label Generation (UPDATED - Economically Grounded)
1. **Primary labels (objective)**: Calculate from realized market behavior
   - **Realized volatility**: Low (<30%), Medium (30-70%), High (>70%) based on actual price moves
   - **Trend strength**: Weak/Strong based on ADX threshold and price slope
   - **Drawdown state**: Normal, Stressed (>10% drawdown), Crash (>20% drawdown)
   - **IV surface state**: Contango (term structure upward), Backwardation (inverted), Neutral
2. **Auxiliary features**: Use existing MarketRegimeDetector output as ONE feature (not ground truth)
3. **Future regime labels**: Shift primary labels forward (1h, 4h, 24h) for prediction task
4. **Validation**: Check if Long Call/Put strategies actually worked in predicted regimes (economic validation)
5. **Data augmentation**: Time warping, magnitude warping for robustness

**CRITICAL FIX**: Original plan trained on rule-based labels only (circular dependency). Now use economically grounded labels derived from realized outcomes.

### Phase 2: Initial Supervised Training

**Training Configuration**:
- **Optimizer**: AdamW (lr=1e-4, weight_decay=1e-5)
- **Batch size**: 64 (fits in RTX 5090 24GB VRAM)
- **Sequence length**: 50 timesteps (lookback)
- **Epochs**: 100 with early stopping (patience=15)
- **Learning rate schedule**: Cosine annealing with warm restarts
- **Validation split**: 20% (time-series cross-validation, not random split)

**Hardware Utilization**:
- **GPU**: PyTorch with CUDA 12.x, mixed precision (FP16) training
- **CPU**: Multi-threaded data loading (14900K supports 32 threads)
- **Batch processing**: Utilize all 24GB VRAM with gradient accumulation if needed

**Training Pipeline**:
```
Data Generator (CPU, 8 workers) → GPU Memory → Forward Pass →
Loss Computation → Backward Pass → Optimizer Step → Metrics Logging
```

**Monitoring**:
- TensorBoard for loss curves, attention weights, embedding visualizations
- Weight & Biases for experiment tracking
- Custom regime transition matrix to visualize prediction quality

### Phase 3: Continual Learning (Self-Improvement)

**Online Adaptation Strategy**: The model improves as new data arrives

#### Mechanism 1: Replay Buffer + Fine-Tuning
1. Every N days (e.g., 7), collect new labeled data
2. Store last M samples (e.g., 1000) in replay buffer
3. Fine-tune model on buffer with reduced learning rate (lr × 0.1)
4. Prevents catastrophic forgetting via experience replay

#### Mechanism 2: Ensemble Model Updates
1. Train new model variant on recent data
2. Maintain ensemble of K models (e.g., 3)
3. Use weighted averaging (recent models weighted higher)
4. Gradually retire oldest model

#### Mechanism 3: Active Learning
1. Track prediction confidence on live data
2. When confidence < threshold (e.g., 0.6), flag for human review
3. Collect ground truth labels from market outcomes
4. Add high-uncertainty samples to training set (targeted data collection)

**Adaptation Frequency**:
- **Daily**: Collect new samples, add to buffer
- **Weekly**: Fine-tune model on recent data
- **Monthly**: Full retraining with expanded dataset
- **Quarterly**: Architecture search for improvements

---

## Feature Engineering Pipeline

### Automated Feature Extraction (coding/core/ml/feature_engineering.py)

```python
class FeatureEngineer:
    """
    Transform raw market data into ML-ready features.
    """

    def extract_features(self, currency: str, timestamp: datetime) -> FeatureVector:
        """
        Extract 100+ features from multiple data sources.
        """

        # Technical indicators (25 features)
        technical = self._extract_technical(currency, timestamp)

        # On-chain metrics (30 features)
        onchain = self._extract_onchain(currency, timestamp)

        # Sentiment signals (10 features)
        sentiment = self._extract_sentiment(currency, timestamp)

        # Market structure (20 features)
        market = self._extract_market_structure(currency, timestamp)

        # Derived features (15 features)
        derived = self._create_derived_features(technical, onchain, sentiment)

        return FeatureVector.concat([technical, onchain, sentiment, market, derived])

    def _extract_technical(self, currency, timestamp):
        # From TechnicalIndicatorCalculator
        return {
            'sma_50': ...,
            'sma_200': ...,
            'rsi': ...,
            'macd': ...,
            'atr_percentile': ...,
            # + 20 more
        }

    def _extract_onchain(self, currency, timestamp):
        # From OnChainAnalyzer + GexDexCalculator
        return {
            'max_pain_distance': ...,
            'put_call_ratio': ...,
            'total_gex': ...,
            'call_wall_strike': ...,
            'itm_call_oi_pct': ...,
            # + 25 more
        }

    def _create_derived_features(self, tech, onchain, sentiment):
        # Cross-product features
        return {
            'gex_times_rsi': onchain['total_gex'] * tech['rsi'],
            'pc_ratio_momentum': onchain['put_call_ratio'] * tech['macd'],
            'fear_greed_delta': sentiment['fear_greed'] - 50,  # centered
            # + 12 more
        }
```

### Feature Normalization

**Per-Feature Scaling**:
- Technical indicators: StandardScaler (z-score normalization)
- Ratios (P/C ratio): RobustScaler (handles outliers)
- Percentiles (ATR percentile): MinMaxScaler (already 0-100)
- Prices: Log transformation + StandardScaler

**Temporal Smoothing**:
- Exponential moving average (alpha=0.1) for noisy features
- Reduces overfitting to intraday noise

---

## Model Implementation Stack

### Core Libraries
```python
# Deep Learning
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset

# Model Architecture
import timm  # Pre-trained backbones if needed
from transformers import AutoModel  # Transformer components

# Training Utilities
import pytorch_lightning as pl  # Training orchestration
from torchmetrics import Accuracy, F1Score, ConfusionMatrix

# Optimization
import optuna  # Hyperparameter tuning

# Interpretability
import captum  # Model interpretability (attention visualization)
import shap  # SHAP values for feature importance

# Experiment Tracking
import wandb  # Weights & Biases
from tensorboard import SummaryWriter
```

### Project Structure
```
coding/
├── core/
│   ├── ml/                          # NEW: ML infrastructure
│   │   ├── models/
│   │   │   ├── regime_detector.py   # Main neural network
│   │   │   ├── encoders.py          # Modal-specific encoders
│   │   │   ├── attention.py         # Attention mechanisms
│   │   │   └── losses.py            # Custom loss functions
│   │   ├── data/
│   │   │   ├── dataset.py           # PyTorch Dataset
│   │   │   ├── dataloader.py        # Data loading pipeline
│   │   │   └── augmentation.py      # Time-series augmentation
│   │   ├── training/
│   │   │   ├── trainer.py           # Training loop
│   │   │   ├── evaluator.py         # Evaluation metrics
│   │   │   └── callbacks.py         # Training callbacks
│   │   ├── inference/
│   │   │   ├── predictor.py         # Live inference
│   │   │   └── ensemble.py          # Model ensemble
│   │   ├── feature_engineering.py   # Feature extraction
│   │   ├── model_registry.py        # Model versioning
│   │   └── config.py                # ML configuration
│   └── analytics/                   # Existing analytics (data source)
├── service/
│   └── ml/                          # NEW: ML services
│       ├── regime_prediction_service.py  # High-level API
│       └── continual_learning_service.py # Online adaptation
└── gui/
    └── tabs/
        └── ml_monitoring_tab.py     # NEW: ML model monitoring UI

models/                              # NEW: Model storage
├── checkpoints/                     # Training checkpoints
├── production/                      # Production models
└── experiments/                     # Experiment artifacts

data/                                # NEW: ML data
├── raw/                            # Raw backfilled data
├── processed/                      # Feature vectors
└── labels/                         # Regime labels
```

---

## Integration with Existing System

### Replacement Strategy (Phased)

**Phase 1: Parallel Evaluation** (Week 1-2)
- Run both rule-based and ML models side-by-side
- Log predictions from both systems
- Compare accuracy, confidence, regime stability
- **No impact on production strategies**

**Phase 2: Hybrid Mode** (Week 3-4)
- Use ML predictions when confidence > 0.75
- Fall back to rule-based when confidence < 0.75
- Weighted ensemble: (ML_prob × 0.7) + (Rule_prob × 0.3)
- **Gradual transition with safety net**

**Phase 3: ML-Primary Mode** (Week 5+)
- ML becomes primary regime detector
- Rule-based serves as sanity check (alert if large divergence)
- **Full ML deployment**

### API Integration

**Modify `MarketRegimeDetector`**:
```python
class MarketRegimeDetector:
    def __init__(self, use_ml: bool = True):
        self.use_ml = use_ml
        if use_ml:
            self.ml_predictor = MLRegimePredictor.load_latest()
        self.rule_based = RuleBasedDetector()  # Existing logic

    def detect_regime(self, currency: str) -> RegimeResult:
        if self.use_ml:
            ml_result = self.ml_predictor.predict(currency)

            # Hybrid confidence check
            if ml_result.confidence > 0.75:
                return ml_result
            else:
                # Fall back to rule-based
                rule_result = self.rule_based.detect(currency)
                return self._ensemble([ml_result, rule_result])
        else:
            return self.rule_based.detect(currency)

    def predict_future_regime(self, currency: str, horizon: str) -> RegimeResult:
        """NEW: Predict future regime (1h, 4h, 24h ahead)"""
        if not self.use_ml:
            raise NotImplementedError("Future prediction requires ML model")

        return self.ml_predictor.predict_future(currency, horizon)
```

**Database Schema Updates**:
```sql
-- Add ML-specific fields to regime_detections table
ALTER TABLE regime_detections ADD COLUMN prediction_source VARCHAR(20);  -- 'rule_based' or 'ml'
ALTER TABLE regime_detections ADD COLUMN ml_confidence DECIMAL(5,4);
ALTER TABLE regime_detections ADD COLUMN regime_probabilities JSONB;  -- Full distribution

-- New table for future predictions
CREATE TABLE regime_predictions (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    predicted_at TIMESTAMP NOT NULL,
    prediction_horizon VARCHAR(10) NOT NULL,  -- '1h', '4h', '24h'
    predicted_regime VARCHAR(20) NOT NULL,
    confidence_score DECIMAL(5,4) NOT NULL,
    regime_probabilities JSONB NOT NULL,
    actual_regime VARCHAR(20),  -- Populated after horizon elapsed
    prediction_accuracy DECIMAL(5,4)  -- Computed post-hoc
);

-- Index for validation queries
CREATE INDEX idx_regime_predictions_validation
ON regime_predictions(predicted_at, prediction_horizon)
WHERE actual_regime IS NOT NULL;
```

---

## Evaluation & Validation Framework

### Offline Metrics (Historical Validation)

**Classification Metrics**:
- **Accuracy**: Overall regime classification correctness
- **F1-Score (macro)**: Balanced performance across all 5 regimes
- **Confusion Matrix**: Identify systematic misclassifications
- **Calibration Plot**: Check if predicted probabilities match actual frequencies

**Prediction Metrics** (Future regime):
- **Directional Accuracy**: Correctly predict bullish vs bearish
- **Regime Persistence Accuracy**: How long predicted regime lasts
- **Early Warning Score**: Lead time before regime transitions

**Confidence Metrics**:
- **ECE (Expected Calibration Error)**: Measure of probability calibration
- **Confidence-Accuracy Curve**: High confidence should correlate with high accuracy

### Online Metrics (Live Monitoring)

**Real-Time Dashboard** (ML Monitoring Tab in GUI):
1. **Current Regime**: ML prediction vs rule-based (side-by-side)
2. **Confidence Timeline**: 24h rolling confidence scores
3. **Feature Importance**: Top 10 features driving current prediction
4. **Attention Heatmap**: Which modalities are active (technical/on-chain/sentiment)
5. **Prediction Horizon**: 1h/4h/24h ahead regime forecasts
6. **Model Performance**: Running accuracy, F1, calibration metrics
7. **Drift Detection**: Alert if input distributions shift (data drift)

**Alerting System**:
- Alert if ML confidence < 0.5 (uncertain regime)
- Alert if ML and rule-based strongly disagree (divergence > 2 regimes)
- Alert if model detects data drift (distribution shift)
- Alert if prediction accuracy drops below baseline

### Backtesting Integration

**Strategy Backtester Enhancement**:
```python
class StrategyBacktester:
    def backtest_with_ml_regime(self, strategy, start_date, end_date):
        """
        Backtest strategy using historical ML regime predictions.
        Compare vs rule-based regime performance.
        """

        results_ml = []
        results_rule = []

        for date in date_range(start_date, end_date):
            # Get historical regime predictions
            ml_regime = ml_predictor.predict_at(date)
            rule_regime = rule_detector.detect_at(date)

            # Evaluate strategy with each regime
            pnl_ml = strategy.evaluate(regime=ml_regime)
            pnl_rule = strategy.evaluate(regime=rule_regime)

            results_ml.append(pnl_ml)
            results_rule.append(pnl_rule)

        # Compare performance
        return {
            'ml_sharpe': calculate_sharpe(results_ml),
            'rule_sharpe': calculate_sharpe(results_rule),
            'ml_win_rate': win_rate(results_ml),
            'rule_win_rate': win_rate(results_rule)
        }
```

---

## Interpretability & Explainability

### Attention Visualization

**Real-Time Attention Dashboard**:
- **Cross-modal attention weights**: Show which modality (technical/on-chain/sentiment) is driving the prediction
- **Feature attribution**: SHAP values for top contributing features
- **Temporal attention**: Highlight which historical timesteps are most relevant

**Example Output**:
```
Current Regime: Weak Bullish (confidence: 82%)

Top Contributing Features:
1. RSI (technical): +0.15 (overbought territory)
2. Put/Call Ratio (on-chain): +0.12 (call-heavy = bullish)
3. Funding Rate (on-chain): +0.08 (positive = longs paying)
4. Fear & Greed (sentiment): -0.05 (moderate greed)
5. Total GEX (on-chain): +0.04 (negative = volatility)

Modal Attention Weights:
- Technical:  45%
- On-Chain:   35%
- Sentiment:  15%
- Market Ctx:  5%

Temporal Focus:
- Recent 6 hours: 70% (short-term momentum driving prediction)
- 24-48h ago: 20% (trend confirmation)
- Older data: 10% (background context)
```

### Model Debugging Tools

**Counterfactual Analysis**:
- "What if RSI was 30 instead of 70?" → How would regime change?
- "What if GEX was positive instead of negative?" → Impact on prediction
- **Use case**: Understand regime triggers and boundary conditions

**Embedding Space Visualization**:
- t-SNE/UMAP projection of learned embeddings
- Color by regime class → See if model learns distinct clusters
- **Use case**: Validate model is learning meaningful representations

---

## Performance Optimization (RTX 5090 Utilization)

### GPU Acceleration Strategies

**Mixed Precision Training** (Automatic FP16):
```python
from torch.cuda.amp import autocast, GradScaler

scaler = GradScaler()

for batch in dataloader:
    optimizer.zero_grad()

    with autocast():  # Automatic FP16 where safe
        outputs = model(batch)
        loss = criterion(outputs, labels)

    scaler.scale(loss).backward()
    scaler.step(optimizer)
    scaler.update()
```
- **Speed boost**: 2-3× faster training
- **Memory savings**: 40-50% less VRAM usage
- **Accuracy**: No degradation with proper scaling

**Gradient Accumulation** (Simulate larger batches):
```python
accumulation_steps = 4  # Effective batch size = 64 × 4 = 256

for i, batch in enumerate(dataloader):
    outputs = model(batch)
    loss = criterion(outputs, labels) / accumulation_steps
    loss.backward()

    if (i + 1) % accumulation_steps == 0:
        optimizer.step()
        optimizer.zero_grad()
```
- **Benefit**: Larger effective batch size for better convergence
- **Trade-off**: Slower iteration (more forward/backward passes)

**Data Parallel Training** (If using multiple GPUs in future):
```python
model = nn.DataParallel(model, device_ids=[0, 1])  # RTX 5090 + future GPU
```

### CPU Optimization (Intel 14900K)

**Multi-threaded Data Loading**:
```python
dataloader = DataLoader(
    dataset,
    batch_size=64,
    num_workers=16,  # Utilize 16 of 32 threads (leave headroom)
    pin_memory=True,  # Faster CPU→GPU transfer
    prefetch_factor=4  # Load 4 batches ahead
)
```

**Feature Engineering Parallelization**:
```python
from multiprocessing import Pool

def extract_features_parallel(timestamps):
    with Pool(processes=24) as pool:  # 24 cores
        features = pool.map(feature_engineer.extract, timestamps)
    return features
```

### Inference Optimization (Production)

**Model Quantization** (INT8):
- Post-training quantization for 4× faster inference
- Minimal accuracy loss (<1% typically)
- Deploy quantized model for real-time predictions

**TorchScript Compilation**:
```python
scripted_model = torch.jit.script(model)
scripted_model.save("regime_detector_compiled.pt")
```
- 10-20% inference speedup
- Removes Python overhead

**Batched Inference**:
- Accumulate requests over short window (e.g., 100ms)
- Process in batch for GPU efficiency
- **Latency**: 100ms batching + 20ms inference = 120ms total

---

## BTC Basis Risk Modeling (NEW - Critical for BTC Options)

### Understanding the Issue

**Critical Fact**: Deribit BTC options reference **BTC futures**, not spot. This introduces **basis risk** (futures-spot spread) that affects Greeks, hedge ratios, and P&L attribution.

**Basis** = Futures Price - Spot Price (or Index Price)

**Why It Matters:**
- Delta calculated against spot is WRONG for BTC options
- Hedge ratios misaligned → systematic P&L leakage
- Basis can widen/narrow independently of spot moves
- Carry cost varies with basis and funding rates

### Implementation Requirements

**1. Data Collection**
- **BTC Index Price**: Deribit's composite index (spot proxy)
- **BTC Futures Price**: Front-month and relevant expiry futures
- **Basis Spread**: Calculate futures - index continuously
- **Implied Repo Rate**: Extract from basis (annualized carry)

**2. Feature Engineering**
- **Basis Level**: Current futures-spot spread (absolute $)
- **Basis Percentage**: Spread as % of spot
- **Basis Trend**: Rate of change (widening/narrowing)
- **Term Structure**: Basis across multiple expiries (contango/backwardation)
- **Correlation**: Basis vs. funding rate correlation

**3. Greeks Adjustment**
- **Underlying Price**: Use futures price (not spot) for BTC option Greeks
- **Delta Hedging**: Hedge with futures or perpetuals (not spot)
- **Basis Greeks**: Track ∂P&L/∂Basis separately from spot delta
- **Validation**: Compare calculated Greeks vs. Deribit mark Greeks

**4. Risk Metrics**
- **Basis Risk Exposure**: Notional exposure to basis moves
- **Stress Scenarios**: Basis shocks (±2%, ±5% of spot)
- **Carry Monitoring**: Daily basis bleed from holding futures hedges

### ETH vs. BTC

**ETH Options**: Also reference futures, but basis typically tighter than BTC. Same logic applies.

**Priority**: HIGH - Affects all BTC/ETH Greeks calculations and hedge effectiveness.

---

## Trading Policy Layer (NEW - Making Regime Model Actionable)

### Why This Matters

Regime classification alone is **not an edge**. Need explicit mapping: **Regime State → Target Greeks → Strategy Families**

This section defines **what to trade** in each regime, making the ML model actionable.

### Volatility Risk Premia (VRP) Framework

**Core Thesis**: Crypto options systematically overprice realized volatility (positive VRP). Deribit research shows ~15 vol points VRP in contango regimes.

**Strategy**: Sell volatility when VRP positive + favorable regime, buy protection in tail-risk regimes.

### Regime-to-Strategy Mapping

| Regime | Market Condition | VRP State | Target Greeks | Strategy Families | Risk Controls |
|--------|------------------|-----------|---------------|-------------------|---------------|
| **Strong Bullish** | High VRP, contango, low stress | Positive (sell vol profitable) | +Theta, -Vega, neutral Gamma | • Short strangles/straddles<br>• Risk-defined spreads (IC)<br>• Gamma scalping (collect theta) | • Max loss caps per position<br>• Tail hedge overlays<br>• Jump risk monitoring |
| **Weak Bullish** | Trend + moderate vol, stable funding | Moderate VRP | +Delta via convexity | • Bull call spreads<br>• Risk reversals (long call, short put)<br>• Calendar spreads | • Basis monitoring<br>• Skew shift protection<br>• Funding rate caps |
| **Sideways** | Range-bound, stable IV, low VRP | Neutral | +Theta, -Gamma (fade moves) | • Iron condors<br>• Butterflies<br>• Short gamma scalping | • Stop on breakout (±10%)<br>• Tight position sizing<br>• Correlation monitoring |
| **Weak Bearish** | Rising uncertainty, funding negative | Low/negative VRP (buy vol) | +Vega, +Gamma | • Long straddles/strangles<br>• Calendar spreads (long vol)<br>• Diagonal spreads | • Theta bleed limits<br>• Exit if vol doesn't rise<br>• Time decay management |
| **Strong Bearish** | Crash risk, correlation→1, liquidations | Negative VRP (protection) | +Gamma, convexity priority | • OTM put protection<br>• Long convex flies<br>• Tail hedges (5-10Δ puts) | • Capital preservation mode<br>• No short gamma<br>• Liquidity priority |

### Position Sizing Framework

**Fractional Kelly**: Use 0.25× Kelly criterion to manage drawdowns
- Full Kelly: max long-term growth, but wild swings
- Quarter-Kelly: cuts vol in half, minimal return loss

**Greek Budgets** (portfolio-level caps):
- **Max Net Delta**: ±20% of portfolio (directional exposure)
- **Max Net Gamma**: ±10 gamma per $1M (tail risk cap)
- **Max Gross Vega**: 50 vega per $1M (IV sensitivity cap)
- **Max Theta Decay**: -2% portfolio per month (carry limit)

### GEX-Driven Tactics

Use Net GEX (Gamma Exposure) from existing system:
- **Positive GEX** (dealers long gamma): Mean-reversion → gamma scalp, range strategies
- **Negative GEX** (dealers short gamma): Momentum → long option breakouts

### Edge Sources (Hypothesis)

1. **VRP Harvesting**: Sell volatility in contango + low-stress regimes
2. **Skew Premia**: Exploit put skew overpricing (25Δ RR trades)
3. **Basis Arbitrage**: Futures-spot spread mean reversion
4. **Microstructure**: Weekend volatility patterns (if validated)
5. **Regime Timing**: Enter vol trades in optimal regime states

### Performance Objectives

- **Target Sharpe**: >1.5 (regime-conditioned strategies vs. 0.8-1.2 unconditional)
- **Max Drawdown**: <20% (vs. 30-40% buy-and-hold)
- **Win Rate**: >55% (regime filter improves entry timing)
- **Turnover**: <50% monthly (avoid over-trading)

**Priority**: HIGH - Required to make regime detection profitable.

---

## Risk Management & Monitoring

### Model Failure Modes

**Degradation Scenarios**:
1. **Data drift**: Market structure changes (e.g., new regulations, ETFs)
2. **Black swan events**: COVID-style shocks (model trained on normal conditions)
3. **Adversarial markets**: High-frequency manipulation
4. **Regime stickiness**: Model predicts regime changes too slowly

**Mitigation Strategies**:
- **Ensemble with rule-based**: Always maintain fallback
- **Confidence thresholds**: Reject low-confidence predictions
- **Drift detection**: Statistical tests on input distributions (KS-test, MMD)
- **Manual override**: GUI toggle to force rule-based mode
- **Regular retraining**: Monthly full retrains with new data

### Monitoring Dashboard Requirements

**Critical Metrics**:
- Model accuracy (rolling 7-day, 30-day)
- Prediction confidence (mean, std dev)
- Feature drift score (0-1, alert if > 0.3)
- Regime transition frequency (alert if > 3 per day)
- Rule-based vs ML agreement rate (alert if < 70%)

**Alerting Thresholds**:
- **High Priority**: Accuracy drops below 60%, confidence < 0.4, severe drift
- **Medium Priority**: Accuracy 60-70%, confidence 0.4-0.5, moderate drift
- **Low Priority**: Accuracy 70-75%, confidence 0.5-0.6, minor drift

---

## Implementation Roadmap

**IMPORTANT**: The roadmap is now split into two phases:
- **Phase 0 (3-6 months)**: Data infrastructure and collection - BLOCKING for ML training
- **Phase 1-4 (8 weeks)**: ML development and deployment - Can only start after Phase 0 completes

---

### Phase 0: Historical Data Backfill (UPDATED - Now Only 1 Week!)

**Duration**: ~1 week (was 3-6 months, now FREE backfill via Deribit API!)

**BREAKTHROUGH**: Deribit Public API provides FREE historical trades for all options since 2018. We can backfill 6-12 months of data in 1 week instead of waiting 3-6 months for continuous collection.

**Critical Prerequisites:**
Before ML training can begin, we need sufficient labeled training data. Current database has only 34 days of ETH data, which is inadequate. Solution: Backfill from Deribit Public API.

**Tasks**:

1. **Backfill Historical Options Trades from Deribit API (PRIMARY TASK)**
   - **Tool**: Use [RiveChen/deribit-historical-data](https://github.com/RiveChen/deribit-historical-data) GitHub repo
   - **API**: `/public/get_last_trades_by_currency_and_time`
   - **Download**: 6-12 months of BTC + ETH options trades (all strikes, expirations)
   - **Data includes**: Trade price, IV (implied volatility), volume, timestamp, instrument
   - **Storage**: deribit_options_data database (new `historical_trades` table)
   - **Estimated time**: 2-3 days (respecting rate limits)

2. **Calculate Greeks from IV**
   - Implement Black-Scholes formula for Greeks calculation
   - Input: IV from trade data, strike, underlying price, time to expiry, risk-free rate
   - Output: Delta, Gamma, Theta, Vega, Rho
   - Validate against live data (compare to existing 34-day dataset)
   - Store in `calculated_greeks` table
   - **Estimated time**: 1-2 days

3. **Aggregate Trades into Hourly Snapshots**
   - Group trades by hour for each instrument (strike/expiration)
   - Calculate volume-weighted average prices (VWAP)
   - **❌ REMOVED: "Infer OI from cumulative volume" - THIS IS WRONG**
   - **✅ CORRECTED: Pull Open Interest directly from `public/get_book_summary_by_instrument` API** (returns `open_interest` field)
   - Collect OI snapshots hourly alongside trade data
   - Calculate bid/ask spread estimates from trade direction
   - Store in `hourly_snapshots` table (similar to option_snapshots schema)
   - **Estimated time**: 1-2 days

   **CRITICAL FIX**: OI (outstanding contracts) ≠ Volume (traded contracts). Must use direct API, not inference.

4. **Expand Database Schema for ML Training**
   - Add `historical_trades` table (raw trade data from API)
   - Add `calculated_greeks` table (Black-Scholes derived Greeks)
   - Add `hourly_snapshots` table (aggregated time-series data)
   - Add `trade_executions` table (for future supervised learning labels)
   - Add `regime_outcomes` table (track regime classification accuracy)
   - Index all tables by currency + timestamp for fast queries
   - **Estimated time**: 1 day

5. **Data Quality Validation**
   - Verify IV values are reasonable (30-150% range)
   - Check Greeks match expected ranges (delta -1 to 1, etc.)
   - Validate no gaps in time-series (hourly continuity)
   - Compare calculated Greeks vs existing 34-day dataset
   - Generate data quality report (completeness, outliers, statistics)
   - **Estimated time**: 1 day

6. **Optional: Implement Continuous Forward Collection** (for ongoing updates)
   - Set up automated daily/hourly capture service
   - Append new trades to historical_trades table
   - Keep dataset current after backfill completes
   - **Estimated time**: 1-2 days (can be done in parallel with ML development)

**Deliverables**:
- ✅ 6-12 months of historical options trade data (BTC + ETH) - FREE from Deribit API
- ✅ Calculated Greeks (delta, gamma, theta, vega) using Black-Scholes
- ✅ Hourly aggregated snapshots (option chains reconstructed)
- ✅ Data quality validation report
- ✅ Database schema optimized for ML training
- ✅ (Optional) Continuous forward collection pipeline

**Data Readiness Criteria** (must meet ALL before proceeding to ML training):
- ✅ Minimum 6 months of data (can get 12+ months for free)
- ✅ Both BTC and ETH coverage
- ✅ Hourly snapshots (reconstructed from trades)
- ✅ Greeks completeness > 90% (calculated for all instruments)
- ✅ IV data 100% (provided by Deribit API)

**Estimated Duration**: ~1 week (7-10 days)
- Day 1-3: Download historical trades from Deribit API
- Day 4-5: Calculate Greeks using Black-Scholes
- Day 6-7: Aggregate into hourly snapshots
- Day 8-9: Data quality validation and schema optimization
- Day 10: (Optional) Set up continuous forward collection

**Cost**: €0 (FREE via Deribit Public API)

**Comparison to Original Plan**:
- **Old approach**: Wait 3-6 months for continuous collection → **Now**: 1 week backfill
- **Old cost**: Unknown (API costs, infrastructure) → **Now**: €0
- **Old data amount**: 3-6 months → **Now**: 6-12 months (or more)

**Recommendation**: Proceed immediately with Deribit API backfill. This is the fastest, cheapest, and most comprehensive solution.

---

### Phase 1: Week 1-2: ML Infrastructure Setup (Can run in parallel with Phase 0)

**Note**: This phase can begin in parallel with Phase 0 data collection. Use existing 34 days of ETH data for prototyping and architecture testing, but DO NOT train production models until Phase 0 completes.

**Tasks**:
1. **Backfill historical data** (if Deribit API supports)
   - OHLCV from Deribit `/get_index_price_history` (attempt 1 year)
   - Funding rates from `/get_funding_rate_history`
   - Fear & Greed Index from Alternative.me historical API
   - Calculate technical indicators using existing `TechnicalIndicatorCalculator`
   - Store in deribit_options_data database
   - **Fallback**: If backfill fails, wait for Phase 0 continuous collection

2. **Feature engineering pipeline** (prototype with 34 days of ETH data)
   - Implement `FeatureEngineer` class
   - Extract 100+ features from historical data
   - Normalize and store feature vectors
   - Validation: Verify features match existing analytics

3. **Label generation** (UPDATED - Economically Grounded)
   - **Primary labels**: Calculate from objective market data
     - Realized volatility buckets (actual price moves): Low/Med/High
     - Trend strength: ADX-based + price slope thresholds
     - Drawdown states: Normal/Stressed/Crash based on max drawdown from recent high
     - IV surface state: Contango/Backwardation in term structure
   - **Auxiliary feature**: Run `MarketRegimeDetector` as ONE input feature (not ground truth)
   - Create future labels (shifted by 1h, 4h, 24h) for prediction task
   - **Validate labels**: Check if Long Call worked in "bullish" regimes, etc.
   - Store in regime_detections table with economic validation scores

   **CRITICAL FIX**: Training purely on rule-based labels creates circular dependency (ML can't exceed rule-based ceiling). Now use objective market outcomes as ground truth.

4. **ML infrastructure setup**
   - Install PyTorch, Lightning, Optuna, W&B
   - Set up project structure (coding/core/ml/)
   - Configure CUDA, verify RTX 5090 access
   - Create data loaders and validation splits

**Deliverables**:
- Backfilled historical data (if possible) OR acknowledgment that Phase 0 must complete
- Feature extraction pipeline (tested on 34-day ETH dataset)
- PyTorch environment configured (RTX 5090 verified)
- Baseline data statistics report (34-day dataset + any backfilled data)
- **Data readiness assessment**: Document current data gaps and Phase 0 completion timeline

### Phase 1: Week 3-4: Model Development & Training (BLOCKED until Phase 0 completes or backfill succeeds)

**PREREQUISITE CHECK**: Before starting this phase, verify:
- ✅ At least 90 days of continuous data available (via backfill OR Phase 0 collection)
- ✅ Both BTC and ETH coverage
- ✅ Hourly capture frequency
- ✅ Data quality metrics acceptable

**If prerequisites NOT met**: Continue Phase 0 data collection, use time for architecture experiments on 34-day dataset (research mode only, not production training)

**Tasks**:
1. **Implement model architecture**
   - Multi-modal encoders (Technical, On-Chain, Sentiment, Market)
   - Cross-modal fusion layer (attention)
   - Temporal modeling (Bi-LSTM + attention)
   - Dual output heads (detection + prediction)

2. **Implement training pipeline**
   - Custom Dataset and DataLoader
   - Multi-task loss function
   - Training loop with early stopping
   - Validation and metric tracking

3. **Hyperparameter tuning**
   - Use Optuna for architecture search
   - Tune: learning rate, batch size, hidden dims, dropout rates
   - Run 50-100 trials, select best config

4. **Initial training**
   - Train for 100 epochs with early stopping
   - Monitor loss curves, accuracy, F1 score
   - Save checkpoints every 5 epochs
   - Generate attention visualizations

**Deliverables**:
- Trained regime detection model (checkpoint)
- Training logs and metrics (W&B dashboard)
- Hyperparameter tuning report
- Attention visualization samples

### Week 5-6: Evaluation & Integration

**Tasks**:
1. **Offline evaluation**
   - Compute classification metrics (accuracy, F1, confusion matrix)
   - Compute prediction metrics (directional accuracy, persistence)
   - Calibration analysis (ECE, reliability diagrams)
   - Compare vs rule-based baseline

2. **Interpretability analysis**
   - SHAP feature importance
   - Attention weight analysis
   - Counterfactual examples
   - Document model behavior patterns

3. **Integration with existing system**
   - Modify `MarketRegimeDetector` for ML mode
   - Add hybrid confidence-based switching
   - Update database schema (prediction_source, ml_confidence)
   - Create `RegimePredictionService` for API access

4. **GUI integration**
   - Add ML Monitoring Tab
   - Real-time regime display (ML vs rule-based)
   - Confidence timeline visualization
   - Feature importance display
   - Manual override toggle

**Deliverables**:
- Evaluation report (ML vs rule-based performance)
- Integrated regime detector (hybrid mode)
- ML Monitoring GUI tab
- User documentation

### Week 7-8: Continual Learning & Production Deployment

**Tasks**:
1. **Implement continual learning**
   - Replay buffer for experience storage
   - Online fine-tuning logic (weekly)
   - Active learning for uncertainty sampling
   - Model versioning and rollback

2. **Production optimization**
   - Model quantization (INT8)
   - TorchScript compilation
   - Batched inference pipeline
   - Latency benchmarking

3. **Monitoring & alerting**
   - Drift detection (statistical tests)
   - Performance degradation alerts
   - Prediction tracking (accuracy vs horizon)
   - Dashboard automation (auto-refresh)

4. **Backtesting integration**
   - Strategy backtester with ML regimes
   - Performance comparison (ML vs rule-based)
   - Regime prediction validation
   - Generate backtest reports

**Deliverables**:
- Continual learning system (automated)
- Production-ready model (quantized, compiled)
- Monitoring dashboard (live metrics)
- Backtest comparison report

### Week 9+: Continuous Improvement

**Tasks**:
- Collect live performance data
- Monthly model retraining
- Feature engineering improvements (based on SHAP analysis)
- Architecture experiments (Transformers, etc.)
- Expand to additional cryptocurrencies
- Research: regime transition prediction, volatility forecasting

---

## Critical Files to Create/Modify

### New Files (ML Infrastructure)

**Core ML Models**:
- `coding/core/ml/models/regime_detector.py` - Main neural network
- `coding/core/ml/models/encoders.py` - Modal-specific encoders
- `coding/core/ml/models/attention.py` - Cross-modal attention
- `coding/core/ml/models/losses.py` - Multi-task loss functions

**Data Pipeline**:
- `coding/core/ml/data/dataset.py` - PyTorch Dataset for regime data
- `coding/core/ml/data/dataloader.py` - Data loading utilities
- `coding/core/ml/data/augmentation.py` - Time-series augmentation

**Training**:
- `coding/core/ml/training/trainer.py` - Training orchestration
- `coding/core/ml/training/evaluator.py` - Evaluation metrics
- `coding/core/ml/training/callbacks.py` - Training callbacks

**Inference**:
- `coding/core/ml/inference/predictor.py` - Live prediction API
- `coding/core/ml/inference/ensemble.py` - Model ensemble logic

**Feature Engineering**:
- `coding/core/ml/feature_engineering.py` - Feature extraction pipeline
- `coding/core/ml/feature_normalization.py` - Scaling and normalization

**Utilities**:
- `coding/core/ml/model_registry.py` - Model versioning and storage
- `coding/core/ml/config.py` - ML configuration (hyperparameters)
- `coding/core/ml/utils.py` - Helper functions

**Services**:
- `coding/service/ml/regime_prediction_service.py` - High-level ML API
- `coding/service/ml/continual_learning_service.py` - Online adaptation
- `coding/service/ml/backfill_service.py` - Historical data collection

**GUI**:
- `coding/gui/tabs/ml_monitoring_tab.py` - ML model monitoring interface

**Scripts**:
- `scripts/backfill_historical_data.py` - Fetch historical data
- `scripts/train_regime_model.py` - Training entry point
- `scripts/evaluate_model.py` - Offline evaluation
- `scripts/deploy_model.py` - Production deployment

**Tests**:
- `tests/unit/ml/test_regime_detector.py` - Model unit tests
- `tests/unit/ml/test_feature_engineering.py` - Feature extraction tests
- `tests/integration/ml/test_training_pipeline.py` - End-to-end training test
- `tests/integration/ml/test_prediction_service.py` - API integration tests

### Modified Files (Integration)

**Core Analytics**:
- `coding/core/analytics/market_regime_detector.py` - Add ML mode, hybrid logic
- `coding/core/database/repository.py` - Add ML prediction queries

**Database**:
- `migrations/005_add_ml_regime_tables.sql` - New schema for ML predictions

**Configuration**:
- `requirements.txt` - Add PyTorch, Lightning, Optuna, SHAP, W&B
- `.env` - Add ML-specific config (model paths, W&B API key)

**Strategy Scoring**:
- `coding/core/strategy/scoring/composite_scorer.py` - Use ML regime with confidence

**Documentation**:
- `CLAUDE.md` - Document ML system architecture and usage
- `documentation/ml_regime_system_guide.md` - ML user guide

---

## Dependencies to Add

```txt
# requirements.txt additions

# Deep Learning Core
torch>=2.2.0
torchvision>=0.17.0
torchaudio>=2.2.0
pytorch-lightning>=2.2.0

# Model Training
optuna>=3.6.0
wandb>=0.16.0
tensorboard>=2.16.0

# Interpretability
shap>=0.44.0
captum>=0.7.0

# Time Series
tsai>=0.3.9  # Time series PyTorch utilities
sktime>=0.26.0  # Time series ML toolkit

# Data Processing
scikit-learn>=1.4.0
scipy>=1.12.0

# Utilities
tqdm>=4.66.0
PyYAML>=6.0.1
```

**System Requirements**:
- **CUDA**: 12.1 or higher (for RTX 5090)
- **PyTorch**: Compiled with CUDA support
- **cuDNN**: 8.9 or higher
- **GPU Memory**: 24GB VRAM (RTX 5090 fully utilized)
- **CPU Threads**: 32 threads (Intel 14900K)
- **RAM**: 64GB recommended (for large batch feature extraction)

---

## Verification & Testing Plan

### Unit Tests
1. **Model architecture**: Forward pass with dummy inputs
2. **Feature extraction**: Output shape and value ranges
3. **Loss functions**: Gradient flow and numerical stability
4. **Data pipeline**: Batch generation, augmentation correctness

### Integration Tests
1. **End-to-end training**: Train for 1 epoch, verify metrics
2. **Prediction API**: Load model, predict regime, check output format
3. **Database integration**: Save/load predictions, query historical
4. **GUI integration**: Render ML tab, display metrics

### Performance Tests
1. **Training throughput**: Samples/sec on RTX 5090
2. **Inference latency**: Time to predict (single sample, batched)
3. **Memory usage**: Peak VRAM during training/inference
4. **CPU utilization**: Multi-threaded data loading efficiency

### End-to-End Validation
1. **Backfill data**: Fetch 3 months of historical data
2. **Train model**: Full training run (100 epochs)
3. **Evaluate**: Offline metrics vs rule-based
4. **Deploy**: Live prediction for 24h
5. **Monitor**: Track accuracy, confidence, drift
6. **Compare**: ML vs rule-based regime agreement

**Success Criteria**:
- ✅ Model trains without errors (convergence within 50 epochs)
- ✅ Validation accuracy > 70% (better than random 20%)
- ✅ F1-score (macro) > 0.65
- ✅ Calibration ECE < 0.10 (well-calibrated probabilities)
- ✅ Inference latency < 50ms (real-time capable)
- ✅ 7-day rolling accuracy > rule-based baseline
- ✅ No crashes or memory leaks during 7-day live test

---

## Risk Mitigation

### Technical Risks

**Risk 1: Insufficient training data (< 3 months)**
- **Impact**: Overfitting, poor generalization
- **Mitigation**: Data augmentation, transfer learning from BTC→ETH, use rule-based as teacher model
- **Fallback**: Start with hybrid mode (ML + rule-based ensemble)

**Risk 2: Model overfits to specific market regime**
- **Impact**: Poor performance when regime shifts
- **Mitigation**: Balanced sampling across regimes, regime-aware validation split, continual learning
- **Fallback**: Confidence-based switching to rule-based

**Risk 3: Catastrophic forgetting during continual learning**
- **Impact**: Model forgets old patterns when adapting to new
- **Mitigation**: Experience replay buffer, elastic weight consolidation, model ensemble
- **Fallback**: Monthly full retrain from scratch

**Risk 4: Prediction latency too high for real-time**
- **Impact**: Stale regime predictions, delayed strategy execution
- **Mitigation**: Model quantization, TorchScript, batched inference, model distillation
- **Fallback**: Pre-compute predictions every 5 minutes (cached)

### Operational Risks

**Risk 1: Data pipeline failure (missing API data)**
- **Impact**: Training halts, predictions become stale
- **Mitigation**: Robust error handling, retry logic, fallback to cached data
- **Fallback**: Use rule-based detector until data restored

**Risk 2: Model deployment bug (production crash)**
- **Impact**: No regime predictions, strategy scoring breaks
- **Mitigation**: Staging environment testing, gradual rollout, automated rollback
- **Fallback**: Instant switch to rule-based mode

**Risk 3: GPU failure (hardware issue)**
- **Impact**: No model training, inference on CPU is slow
- **Mitigation**: CPU-compatible model checkpoint, pre-trained model cache
- **Fallback**: Rule-based detector, repair GPU offline

**Risk 4: Model drift goes undetected**
- **Impact**: Degraded accuracy without alerts
- **Mitigation**: Automated drift detection, daily performance reports, human-in-the-loop validation
- **Fallback**: Manual model rollback to previous version

---

## Expected Outcomes

### Phase 0 Completion (Month 3-6)
- **Data infrastructure** with automated hourly captures (BTC + ETH)
- **90-180 days** of continuous time-series data
- **Trade execution labels** for supervised learning
- **Data quality monitoring** dashboard
- **Readiness for ML training** (all prerequisites met)

### Short-Term (Month 6-8 after Phase 0, or Month 1-2 if backfill succeeds)
- **Trained model** with 75%+ accuracy on historical data
- **Hybrid regime detector** (ML + rule-based fallback)
- **ML Monitoring GUI** showing live predictions and confidence
- **Validation report** comparing ML vs rule-based performance

### Medium-Term (Month 9-11 after Phase 0)
- **Continual learning** system adapting to new data weekly
- **Prediction accuracy** consistently > 80% (7-day rolling)
- **Strategy scoring** improved with ML regimes (higher Sharpe ratio in backtests)
- **Feature importance** insights identifying novel market signals

### Long-Term (Month 12+ after Phase 0)
- **Multi-horizon prediction** (1h, 4h, 24h) with 70%+ directional accuracy
- **Regime transition forecasting** with early warning signals
- **Multi-asset models** (BTC, ETH, SOL, etc. trained jointly)
- **Institutional-grade system** with interpretability and robustness

### Success Metrics
- **Accuracy**: ML regime detection > 80% (vs 65% rule-based)
- **F1-Score**: Macro F1 > 0.75 (balanced across all regimes)
- **Calibration**: ECE < 0.08 (confidence matches accuracy)
- **Latency**: Inference < 50ms (real-time capable)
- **Uptime**: 99.9% availability (including fallback)
- **Strategy Performance**: Sharpe ratio +15% vs rule-based regimes

---

## Conclusion

This ML-powered regime detection system represents a **cutting-edge, production-ready architecture** that fully leverages your Intel 14900K + RTX 5090 hardware. The multi-modal temporal fusion network with continual learning provides institutional-grade predictions while maintaining interpretability and robustness.

**Key Innovations**:
1. **Multi-modal fusion**: Learns complex relationships between technical, on-chain, and sentiment data
2. **Dual-task learning**: Simultaneous detection (current) and prediction (future)
3. **Self-improving**: Continual learning ensures model stays current
4. **Interpretable**: Attention weights and SHAP values explain predictions
5. **Production-ready**: Hybrid mode, monitoring, drift detection, fallback strategies

**Timeline**:
- **Phase 0 (Data Backfill)**: ~1 week (FREE from Deribit API) ✅
- **Phase 1-4 (ML Development)**: 8 weeks after Phase 0 completes
- **Total**: **9-10 weeks (~2.5 months)** 🎉

**Cost**: **€0** (completely free via Deribit Public API)

**Expected Performance**: 80%+ accuracy, <50ms latency, 99.9% uptime

**IMPLEMENTATION PATH** (Updated):
1. **Week 1**: Backfill 6-12 months of historical options trades from Deribit API (FREE)
2. **Week 1**: Calculate Greeks using Black-Scholes, aggregate into hourly snapshots
3. **Week 2-9**: ML model development, training, and deployment
4. **Week 10**: Production deployment with monitoring

This system will transform your strategy evaluation from reactive (what is the regime?) to predictive (what will the regime be?), enabling proactive position management and superior risk-adjusted returns. The **Deribit API backfill solution** provides the quality data foundation needed for institutional-grade ML training at **zero cost**.
