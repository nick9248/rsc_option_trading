"""
ML Model Store.

Save and load trained models with metadata and versioning.
"""

import json
import logging
import joblib
from datetime import datetime
from pathlib import Path
from typing import Any, Tuple, List, Optional, Dict
from pydantic import BaseModel, Field
import uuid

logger = logging.getLogger(__name__)


class ModelMetadata(BaseModel):
    """Metadata saved alongside each model."""
    model_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    model_type: str  # "classifier" or "regressor"
    target: str  # "market_regime" or "realized_vol_24h"
    currency: str
    created_at: datetime = Field(default_factory=datetime.now)
    training_start: datetime
    training_end: datetime
    n_samples: int
    feature_names: List[str]
    walk_forward_metrics: Dict[str, float]
    lightgbm_params: Dict
    version: int = 1


class ModelStore:
    """Save and load trained models with metadata."""

    def __init__(self, model_dir: str = "models"):
        """
        Initialize model store.

        Args:
            model_dir: Directory to store models (relative to project root).
        """
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        self.registry_path = self.model_dir / "model_registry.json"

        logger.info(f"ModelStore initialized: {self.model_dir.absolute()}")

    def save(
        self,
        model: Any,
        metadata: ModelMetadata
    ) -> Path:
        """
        Save model + metadata to disk.

        Storage structure:
        models/
        ├── BTC_market_regime_20260207_v1/
        │   ├── model.joblib
        │   └── metadata.json
        └── model_registry.json

        Args:
            model: Trained LightGBM model.
            metadata: Model metadata.

        Returns:
            Path to saved model directory.
        """
        # Create directory name
        timestamp = metadata.created_at.strftime("%Y%m%d_%H%M%S")
        dir_name = f"{metadata.currency}_{metadata.target}_{timestamp}_v{metadata.version}"
        model_path = self.model_dir / dir_name
        model_path.mkdir(parents=True, exist_ok=True)

        # Save model
        model_file = model_path / "model.joblib"
        joblib.dump(model, model_file)
        logger.info(f"Saved model to {model_file}")

        # Save metadata
        metadata_file = model_path / "metadata.json"
        with open(metadata_file, 'w') as f:
            json.dump(metadata.model_dump(mode='json'), f, indent=2, default=str)
        logger.info(f"Saved metadata to {metadata_file}")

        # Update registry
        self._update_registry(metadata, model_path)

        logger.info(f"Model saved successfully: {dir_name}")

        return model_path

    def load(
        self,
        model_id: Optional[str] = None,
        currency: Optional[str] = None,
        target: Optional[str] = None,
        latest: bool = True
    ) -> Tuple[Any, ModelMetadata]:
        """
        Load model + metadata from disk.

        Args:
            model_id: Specific model ID to load.
            currency: Filter by currency.
            target: Filter by target.
            latest: If True, loads most recent model matching filters.

        Returns:
            (model, metadata) tuple.

        Raises:
            FileNotFoundError: If no matching model found.
        """
        if model_id:
            # Load specific model by ID
            registry = self._load_registry()
            matching = [m for m in registry if m['model_id'] == model_id]

            if not matching:
                raise FileNotFoundError(f"Model with ID {model_id} not found")

            metadata_dict = matching[0]

        else:
            # Find matching models
            registry = self._load_registry()

            matching = registry
            if currency:
                matching = [m for m in matching if m['currency'] == currency]
            if target:
                matching = [m for m in matching if m['target'] == target]

            if not matching:
                filters = []
                if currency:
                    filters.append(f"currency={currency}")
                if target:
                    filters.append(f"target={target}")
                raise FileNotFoundError(f"No models found with {', '.join(filters)}")

            # Sort by created_at and take latest
            matching = sorted(matching, key=lambda x: x['created_at'], reverse=True)
            metadata_dict = matching[0] if latest else matching[-1]

        # Load metadata
        metadata = ModelMetadata(**metadata_dict)

        # Reconstruct path
        timestamp = metadata.created_at.strftime("%Y%m%d_%H%M%S")
        dir_name = f"{metadata.currency}_{metadata.target}_{timestamp}_v{metadata.version}"
        model_path = self.model_dir / dir_name

        if not model_path.exists():
            raise FileNotFoundError(f"Model directory not found: {model_path}")

        # Load model
        model_file = model_path / "model.joblib"
        if not model_file.exists():
            raise FileNotFoundError(f"Model file not found: {model_file}")

        model = joblib.load(model_file)
        logger.info(f"Loaded model from {model_file}")

        return model, metadata

    def list_models(self) -> List[ModelMetadata]:
        """
        List all saved models with their metadata.

        Returns:
            List of ModelMetadata objects.
        """
        registry = self._load_registry()
        return [ModelMetadata(**m) for m in registry]

    def _update_registry(self, metadata: ModelMetadata, model_path: Path) -> None:
        """
        Update model registry with new model.

        Args:
            metadata: Model metadata.
            model_path: Path to model directory.
        """
        registry = self._load_registry()

        # Add new entry
        entry = metadata.model_dump(mode='json')
        entry['path'] = str(model_path.relative_to(self.model_dir))

        registry.append(entry)

        # Save registry
        with open(self.registry_path, 'w') as f:
            json.dump(registry, f, indent=2, default=str)

        logger.info(f"Updated model registry ({len(registry)} models)")

    def _load_registry(self) -> List[Dict]:
        """
        Load model registry.

        Returns:
            List of model metadata dictionaries.
        """
        if not self.registry_path.exists():
            return []

        with open(self.registry_path, 'r') as f:
            return json.load(f)
