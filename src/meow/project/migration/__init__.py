"""Automatic conversion of a legacy project config to `.meow/config.toml`."""

from .convert import migrate_legacy_config

__all__ = ["migrate_legacy_config"]
