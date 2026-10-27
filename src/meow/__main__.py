"""Allow ``python -m meow`` as an alias for the ``meow`` console command."""

from meow.common.safe_platform import install_safe_platform

install_safe_platform()

from meow.cli.cli import cli_main  # ruff: ignore[module-import-not-at-top-of-file] -- after the platform install

if __name__ == "__main__":
    cli_main()
