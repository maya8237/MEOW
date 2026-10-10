#!/usr/bin/env bash

set -euo pipefail

installation=${1:-fresh}
case "$installation" in
    fresh|preinstalled) ;;
    *) printf '%s\n' 'Expected fresh or preinstalled.' >&2; exit 1 ;;
esac

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
temp_home="$(mktemp -d)"
output_file="$(mktemp)"

cleanup() {
    rm -rf -- "$temp_home"
    rm -f -- "$output_file"
}

trap cleanup EXIT

if [ ! -e "$repo_dir/.git" ] || [ ! -d "$repo_dir/skills" ]; then
    printf '%s\n' 'The CI checkout is not a MEOW repository.' >&2
    exit 1
fi

run_installer() {
    printf '%s\n' "$@" | sh -c 'eval "$(cat "$1")"' _ "$repo_dir/scripts/install.sh"
}

if ! (
    export HOME="$temp_home"
    unset PYTHONPATH
    cd -- "$repo_dir"
    if [ "$installation" = fresh ]; then
        if command -v meow >/dev/null 2>&1; then
            printf '%s\n' 'MEOW must not be on PATH before the installer runs.' >&2
            exit 1
        fi
        python -c "import importlib.util; assert importlib.util.find_spec('meow') is None, 'MEOW must not be importable before the installer runs.'" || exit 1
        # The streamed local script must install the checkout itself.
        run_installer "$repo_dir" n || exit 1
    else
        command -v meow >/dev/null || exit 1
        python -c 'import meow' || exit 1
        run_installer n n || exit 1
    fi

    python -c "import importlib.metadata as m; from pathlib import Path; import meow, sys; assert Path(meow.__file__).resolve() == Path(sys.argv[1], 'src/meow/__init__.py').resolve(); print('Installed MEOW ' + m.version('meow'))" "$repo_dir" || exit 1
    meow --version || exit 1
) >"$output_file" 2>&1; then
    cat "$output_file"
    exit 1
fi

cat "$output_file"

if [ "$installation" = fresh ] && ! grep -Fq 'Installing MEOW with' "$output_file"; then
    printf '%s\n' 'The installer did not install MEOW from the checkout.' >&2
    exit 1
fi

if [ "$installation" = preinstalled ] && grep -Fq 'Installing ' "$output_file"; then
    printf '%s\n' 'The installer attempted to reinstall MEOW.' >&2
    exit 1
fi

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
