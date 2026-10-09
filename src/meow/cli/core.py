"""Compatibility shim for legacy ``meow.cli.core`` imports."""

from . import cli as _cli

for _name in dir(_cli):
    if _name != "__builtins__":
        globals()[_name] = getattr(_cli, _name)


def cli_main(*args, **kwargs):
    """Run the CLI while keeping legacy-shim monkeypatches effective."""
    if "launch_background" in globals():
        _cli.launch_background = globals()["launch_background"]
    return _cli.cli_main(*args, **kwargs)
