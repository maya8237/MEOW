#!/usr/bin/env bash

set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
temp_home="$(mktemp -d)"
output_file="$(mktemp)"

cleanup() {
    rm -rf -- "$temp_home"
    rm -f -- "$output_file"
}

trap cleanup EXIT

if ! command -v meow >/dev/null 2>&1; then
    printf '%s\n' 'The editable MEOW install is not on PATH.' >&2
    exit 1
fi

if [ ! -d "$repo_dir/.git" ] || [ ! -d "$repo_dir/skills" ]; then
    printf '%s\n' 'The CI checkout is not a MEOW repository.' >&2
    exit 1
fi

if ! (
    export HOME="$temp_home"
    export PYTHONPATH="$repo_dir/src"
    printf 'n\n' | sh "$repo_dir/scripts/install.sh" >"$output_file" 2>&1
); then
    cat "$output_file"
    exit 1
fi

cat "$output_file"

if ! grep -Fq 'Done!' "$output_file"; then
    printf '%s\n' 'The installer did not report success.' >&2
    exit 1
fi

if grep -Fq 'Cloning MEOW' "$output_file"; then
    printf '%s\n' 'The installer attempted to clone despite the CI checkout.' >&2
    exit 1
fi

if ! grep -Fq 'existing MEOW checkout' "$output_file"; then
    printf '%s\n' 'The installer did not use the CI checkout.' >&2
    exit 1
fi
