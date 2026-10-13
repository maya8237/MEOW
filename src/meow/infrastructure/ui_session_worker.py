"""Fixed entry point for detached UI session supervisors."""

from __future__ import annotations

import argparse
from pathlib import Path

from meow.infrastructure.ui_sessions import worker_main


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("session_id")
    parser.add_argument("attempt_id")
    parser.add_argument("token")
    args = parser.parse_args()
    return worker_main(args.repo, args.session_id, args.attempt_id, args.token)


if __name__ == "__main__":
    raise SystemExit(main())
