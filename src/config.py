"""
Global configuration loader and market threshold constants.
Loads settings from config/settings.yaml with safe fallbacks.
"""

from pathlib import Path
from typing import Any, Dict
import yaml


DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
DEFAULT_FALLBACK_INTERVALS_PATH = Path(__file__).resolve().parent.parent / "config" / "fallback_intervals.yaml"


def load_settings(path: Path | str = DEFAULT_SETTINGS_PATH) -> Dict[str, Any]:
    """Load configuration dictionary from YAML settings file."""
    p = Path(path)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def load_fallback_intervals(path: Path | str = DEFAULT_FALLBACK_INTERVALS_PATH) -> Dict[str, Any]:
    """Load fallback intervals catalog dictionary from YAML file."""
    p = Path(path)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


# Module-level cached settings dictionary
_SETTINGS: Dict[str, Any] = load_settings()
FALLBACK_INTERVALS: Dict[str, Any] = load_fallback_intervals()


def get_settings() -> Dict[str, Any]:
    """Retrieve current settings dictionary."""
    global _SETTINGS
    if not _SETTINGS:
        _SETTINGS = load_settings()
    return _SETTINGS


def reload_settings(path: Path | str = DEFAULT_SETTINGS_PATH) -> Dict[str, Any]:
    """Force reload settings from disk and update cached constants."""
    global _SETTINGS, URGENT_PCT_DIFF, MODERATE_PCT_DIFF, URGENT_LEAD_DAYS, BASE_PERCENTILE, CLEANING_FEE
    global COMP_PRICE_WEIGHT, HISTORICAL_PRICE_WEIGHT, WEEKEND_PREMIUM_FACTOR, MIN_PRICE_CHANGE_PCT
    global BAYESIAN_SHRINKAGE_K, MIN_SAMPLE_SIZE, ENFORCE_MONOTONIC_TAPERING, OPERATIONAL_FLOORS, LEAD_TIME_MATRIX
    _SETTINGS = load_settings(path)
    FALLBACK_INTERVALS.clear()
    FALLBACK_INTERVALS.update(load_fallback_intervals())
    _strat = _SETTINGS.get("strategy") or {}
    _prop = _SETTINGS.get("property") or {}
    _anomaly = _strat.get("anomaly_thresholds") or {}
    URGENT_PCT_DIFF = float(_anomaly.get("urgent_percent_diff", 25.0))
    MODERATE_PCT_DIFF = float(_anomaly.get("moderate_percent_diff", 10.0))
    URGENT_LEAD_DAYS = int(_anomaly.get("urgent_lead_days", 60))
    BASE_PERCENTILE = float(_strat.get("base_percentile", 65.0))
    CLEANING_FEE = float(_prop.get("cleaning_fee", 500.0))
    _pricing_cfg = _strat.get("proposed_pricing") or {}
    COMP_PRICE_WEIGHT = float(_pricing_cfg.get("comp_weight", 0.67))
    HISTORICAL_PRICE_WEIGHT = float(_pricing_cfg.get("historical_weight", 0.33))
    WEEKEND_PREMIUM_FACTOR = float(_pricing_cfg.get("weekend_premium_factor", 1.50))
    MIN_PRICE_CHANGE_PCT = float(_pricing_cfg.get("min_price_change_pct", 0.05))
    BAYESIAN_SHRINKAGE_K = float(_strat.get("bayesian_shrinkage_k", 5.0))
    MIN_SAMPLE_SIZE = int(_strat.get("min_empirical_sample_size", 5))
    ENFORCE_MONOTONIC_TAPERING = bool(_strat.get("enforce_monotonic_tapering", True))
    OPERATIONAL_FLOORS = dict(_strat.get("operational_floors") or {"weekend": 450.0, "midweek": 300.0})
    LEAD_TIME_MATRIX = dict(_strat.get("lead_time_matrix") or {})
    return _SETTINGS


_strat_config = _SETTINGS.get("strategy") or {}
_prop_config = _SETTINGS.get("property") or {}
_anomaly_config = _strat_config.get("anomaly_thresholds") or {}

# Market gap threshold constants
# - On Target: |diff| < MODERATE_PCT_DIFF
# - Review: MODERATE_PCT_DIFF <= |diff| < URGENT_PCT_DIFF
# - Urgent Action: |diff| >= URGENT_PCT_DIFF
URGENT_PCT_DIFF: float = float(_anomaly_config.get("urgent_percent_diff", 25.0))
MODERATE_PCT_DIFF: float = float(_anomaly_config.get("moderate_percent_diff", 10.0))
URGENT_LEAD_DAYS: int = int(_anomaly_config.get("urgent_lead_days", 60))

# Property and pricing strategy constants
BASE_PERCENTILE: float = float(_strat_config.get("base_percentile", 65.0))
CLEANING_FEE: float = float(_prop_config.get("cleaning_fee", 500.0))

# Proposed pricing strategy constants (2:1 weighted competitive pricing)
_pricing_config = _strat_config.get("proposed_pricing") or {}
COMP_PRICE_WEIGHT: float = float(_pricing_config.get("comp_weight", 0.67))
HISTORICAL_PRICE_WEIGHT: float = float(_pricing_config.get("historical_weight", 0.33))
WEEKEND_PREMIUM_FACTOR: float = float(_pricing_config.get("weekend_premium_factor", 1.50))
MIN_PRICE_CHANGE_PCT: float = float(_pricing_config.get("min_price_change_pct", 0.05))

# Dynamic 2D Strategy Matrix and Bayesian Shrinkage constants
BAYESIAN_SHRINKAGE_K: float = float(_strat_config.get("bayesian_shrinkage_k", 5.0))
MIN_SAMPLE_SIZE: int = int(_strat_config.get("min_empirical_sample_size", 5))
ENFORCE_MONOTONIC_TAPERING: bool = bool(_strat_config.get("enforce_monotonic_tapering", True))
OPERATIONAL_FLOORS: Dict[str, float] = dict(_strat_config.get("operational_floors") or {"weekend": 450.0, "midweek": 300.0})
LEAD_TIME_MATRIX: Dict[str, Any] = dict(_strat_config.get("lead_time_matrix") or {})
