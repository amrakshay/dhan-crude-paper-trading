"""
YAML configuration access.

The config directory comes from the CONFIG_PATH env var (default ./conf).
`default-config.yaml` is loaded first; `local-config.yaml`, if present, is
deep-merged on top. String values support ``${ENV_VAR}`` and
``${ENV_VAR:fallback}`` substitution so secrets stay in the environment.
"""
import os
import re
import threading
from typing import Any, Dict, List, Optional

import yaml

_CONFIG: Dict[str, Any] = {}
_LOCK = threading.Lock()
_LOADED = False

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")

DEFAULT_CONFIG_FILE = "default-config.yaml"
LOCAL_CONFIG_FILE = "local-config.yaml"


def get_config_path() -> str:
    """Directory holding the YAML config files."""
    return os.environ.get("CONFIG_PATH", os.path.join(os.getcwd(), "conf"))


def _substitute_env(value: Any) -> Any:
    """Recursively expand ${VAR} / ${VAR:default} inside strings."""
    if isinstance(value, str):
        def _replace(match: "re.Match") -> str:
            var_name, fallback = match.group(1), match.group(2)
            return os.environ.get(var_name, fallback if fallback is not None else "")

        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `override` onto `base`, recursing into nested dicts."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Load (once) and return the merged configuration."""
    global _CONFIG, _LOADED

    with _LOCK:
        if _LOADED and not force:
            return _CONFIG

        directory = config_path or get_config_path()
        default_file = os.path.join(directory, DEFAULT_CONFIG_FILE)
        if not os.path.exists(default_file):
            raise FileNotFoundError(
                f"Configuration file not found: {default_file}. "
                f"Set CONFIG_PATH to the directory holding {DEFAULT_CONFIG_FILE}."
            )

        with open(default_file, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}

        local_file = os.path.join(directory, LOCAL_CONFIG_FILE)
        if os.path.exists(local_file):
            with open(local_file, "r", encoding="utf-8") as handle:
                config = _deep_merge(config, yaml.safe_load(handle) or {})

        _CONFIG = _substitute_env(config)
        _LOADED = True
        return _CONFIG


def _resolve(key: str) -> Any:
    """Walk a dotted key through the config tree. Returns a sentinel if absent."""
    node: Any = load_config()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


class _Missing:
    pass


_MISSING = _Missing()


def get_property_value(key: str, default: Optional[str] = None) -> Optional[str]:
    """String property by dotted key, e.g. ``database.url``."""
    value = _resolve(key)
    if isinstance(value, _Missing) or value is None:
        return default
    if isinstance(value, str) and value == "":
        return default
    return str(value)


def get_property_value_int(key: str, default: int = 0) -> int:
    value = _resolve(key)
    if isinstance(value, _Missing) or value is None or value == "":
        return default
    return int(value)


def get_property_value_float(key: str, default: float = 0.0) -> float:
    value = _resolve(key)
    if isinstance(value, _Missing) or value is None or value == "":
        return default
    return float(value)


def get_property_value_boolean(key: str, default: bool = False) -> bool:
    value = _resolve(key)
    if isinstance(value, _Missing) or value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y", "on")


def get_property_value_list(key: str, default: Optional[List[Any]] = None) -> List[Any]:
    value = _resolve(key)
    if isinstance(value, _Missing) or value is None:
        return list(default or [])
    if isinstance(value, list):
        return value
    return [part.strip() for part in str(value).split(",") if part.strip()]


def get_property_dict(key: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    value = _resolve(key)
    if isinstance(value, _Missing) or not isinstance(value, dict):
        return dict(default or {})
    return value
