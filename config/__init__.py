"""Compatibilidade com imports existentes de config.py.

Re-exporta ThresholdConfig e load_config do modulo legado
para que `from config import ThresholdConfig, load_config` continue funcionando.
"""
from config_legacy import ThresholdConfig, load_config  # noqa: F401
