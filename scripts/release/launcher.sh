#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tag=$(cat "$root/current-version")
case "$tag" in
    ''|*[!v0-9.]*|*/*) printf '%s\n' 'Invalid MEOW release pointer. Rerun the installer.' >&2; exit 1 ;;
esac
exec "$root/versions/$tag/runtime/bin/python3" -m meow "$@"
