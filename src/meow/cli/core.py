"""Compatibility shim for legacy ``meow.cli.core`` imports."""

from . import cli as _cli

for _name in dir(_cli):
    if _name != "__builtins__":
        globals()[_name] = getattr(_cli, _name)
