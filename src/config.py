"""
Global configuration loader and market threshold constants.
Loads settings from config/settings.yaml with safe fallbacks.
"""

from pathlib import Path
from typing import Any, Dict
import yaml


DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


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


# Module-level cached settings dictionary
_SETTINGS: Dict[str, Any] = load_settings()


def get_settings() -> Dict[str, Any]:
    """Retrieve current settings dictionary."""
    global _SETTINGS
    if not _SETTINGS:
        _SETTINGS = load_settings()
    return _SETTINGS


def reload_settings(path: Path | str = DEFAULT_SETTINGS_PATH) -> Dict[str, Any]:
    """Force reload settings from disk and update cached constants."""
    global _SETTINGS, URGENT_PCT_DIFF, MODERATE_PCT_DIFF, URGENT_LEAD_DAYS, BASE_PERCENTILE, CLEANING_FEE
    _SETTINGS = load_settings(path)
    _anomaly = _SETTINGS.get("strategy", {}).get("anomaly_thresholds", {})
    URGENT_PCT_DIFF = float(_anomaly.get("urgent_percent_diff", 25.0))
    MODERATE_PCT_DIFF = float(_anomaly.get("moderate_percent_diff", 10.0))
    URGENT_LEAD_DAYS = int(_anomaly.get("urgent_lead_days", 60))
    BASE_PERCENTILE = float(_SETTINGS.get("strategy", {}).get("base_percentile", 65.0))
    CLEANING_FEE = float(_SETTINGS.get("property", {}).get("cleaning_fee", 500.0))
    return _SETTINGS


_anomaly_config = _SETTINGS.get("strategy", {}).get("anomaly_thresholds", {})

# Market gap threshold constants
# - On Target: |diff| < MODERATE_PCT_DIFF
# - Review: MODERATE_PCT_DIFF <= |diff| < URGENT_PCT_DIFF
# - Urgent Action: |diff| >= URGENT_PCT_DIFF
URGENT_PCT_DIFF: float = float(_anomaly_config.get("urgent_percent_diff", 25.0))
MODERATE_PCT_DIFF: float = float(_anomaly_config.get("moderate_percent_diff", 10.0))
URGENT_LEAD_DAYS: int = int(_anomaly_config.get("urgent_lead_days", 60))

# Property and pricing strategy constants
BASE_PERCENTILE: float = float(_SETTINGS.get("strategy", {}).get("base_percentile", 65.0))
CLEANING_FEE: float = float(_SETTINGS.get("property", {}).get("cleaning_fee", 500.0))
