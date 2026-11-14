"""
Decision maker: a deterministic, auditable scenario engine over structured evidence.

Every signal is converted into an ``EvidenceItem`` (``evidence.py``); the engine (``scenario_engine.py``) builds a price
map (``price_map.py``) and scores scenarios from it. All weights and thresholds live in ``decision_config.py``. Nothing
here is calibrated against outcomes yet: scores are relative support on a stated scale, never probabilities.
"""
