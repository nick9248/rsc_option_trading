"""
Decision maker, now an evidence report: structured evidence from every source, the price map built from its levels, and the
caveats. Every signal is converted into an ``EvidenceItem`` (``evidence.py``); ``evidence_report.py`` applies the freshness check and
builds the map (``price_map.py``). All weights and thresholds live in ``decision_config.py``. It does not score scenarios or label
confidence (removed 2026-11-21: never calibrated against outcomes); the strategy suggestion is a separate, later step.
"""
