"""
Config loader. Reads config.yaml and exposes it as a dict-like object.
Single source of truth for all parameters.
"""
import os
import yaml
from pathlib import Path


class Config:
    _instance = None
    _data = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        config_path = Path(__file__).parent.parent / "config" / "config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        with open(config_path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

    def get(self, *keys, default=None):
        """Nested key access: config.get('strategy', 'lot_size')"""
        node = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def all(self):
        return self._data


# Singleton instance for easy import
config = Config()
