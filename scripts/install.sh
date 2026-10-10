#!/usr/bin/env sh
# Streamable MEOW installer. Run with:
# curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
#
# With an existing `meow` on PATH it offers to update that checkout and re-runs
# the post-install setup; otherwise it clones MEOW over SSH, installs it
# editable, and runs the post-install setup.

set -eu

repository_url='git@github.com:maya8237/MEOW.git'
origin_pyproject_url='https://raw.githubusercontent.com/maya8237/MEOW/main/pyproject.toml'
default_parent=${HOME:-"$(pwd)"}
python_candidates='python3.15 python3.14 python3.13 python3.12 python3 python'
failure_reported=0
reply=''
existing_python=''
existing_checkout=''

show_banner() {
    printf '\033[36m\n'
    printf '%s\n' '  +-----------------------------------------------------+'
    printf '%s\n' '  |                   MEOW INSTALLER                    |'
    printf '%s\n' '  |  Management, Execution & Optimization of Workflows  |'
    printf '%s\n' '  +-----------------------------------------------------+'
    printf '\033[0m\n'
}

report_unexpected_failure() {
    status=$?
    if [ "$status" -ne 0 ] && [ "$failure_reported" -eq 0 ]; then
        printf '\033[31mFailed: installer exited with status %s.\033[0m\n' "$status" >&2
    fi
}

trap report_unexpected_failure 0
trap 'exit 130' HUP INT TERM

die() {
    failure_reported=1
    printf '\033[31mFailed: %s\033[0m\n' "$*" >&2
    exit 1
}

has_tty() {
    [ -r /dev/tty ] && (: </dev/tty) 2>/dev/null
}

# Read one line into $reply. When the script itself is piped into sh
# (curl ... | sh) stdin is the script, so prompts must come from the terminal.
ask() {
    printf '%s' "$1"
    reply=''
    if has_tty; then
        IFS= read -r reply </dev/tty || reply=''
    else
        IFS= read -r reply || reply=''
    fi
}

# Like ask, but with Tab completion of paths when a terminal and bash exist.
ask_path() {
    if has_tty && command -v bash >/dev/null 2>&1; then
        reply=$(bash -c 'IFS= read -e -r -p "$1" answer </dev/tty >/dev/tty 2>&1; printf "%s" "$answer"' _ "$1 (Tab autocompletes paths): ") \
            || reply=''
    else
        ask "$1: "
    fi
}

# Run a command under a spinner when attached to a terminal. The command's
# output is hidden on success and replayed on failure; without a terminal it
# streams straight through.
run_with_spinner() (
    spinner_message=$1
    shift

    if [ ! -t 1 ]; then
        printf '  %s...\n' "$spinner_message"
        if "$@"; then spinner_status=0; else spinner_status=$?; fi
    else
        spinner_chars='|/-\'
        spinner_log=$(mktemp)
        printf '\033[?25l'
        "$@" >"$spinner_log" 2>&1 &
        spinner_pid=$!
        trap 'kill "$spinner_pid" 2>/dev/null; rm -f "$spinner_log"; printf "\033[?25h\n"; exit 130' HUP INT TERM

        while kill -0 "$spinner_pid" 2>/dev/null; do
            spinner_char=${spinner_chars%"${spinner_chars#?}"}
            printf '\r  \033[36m[%s]\033[0m %s' "$spinner_char" "$spinner_message"
            spinner_chars=${spinner_chars#?}$spinner_char
            sleep 0.1 2>/dev/null || sleep 1
        done

        if wait "$spinner_pid"; then spinner_status=0; else spinner_status=$?; fi
        printf '\033[?25h\r\033[2K'
        [ "$spinner_status" -eq 0 ] || cat "$spinner_log" >&2
        rm -f "$spinner_log"
    fi

    if [ "$spinner_status" -eq 0 ]; then
        printf '\033[32m  [OK] %s\033[0m\n' "$spinner_message"
    else
        printf '\033[31m  [FAIL] %s\033[0m\n' "$spinner_message"
    fi
    exit "$spinner_status"
)

is_supported_python() {
    command -v "$1" >/dev/null 2>&1 \
        && "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1
}

select_python() {
    for candidate in $python_candidates; do
        if is_supported_python "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

# Sets existing_python/existing_checkout to the interpreter and git checkout
# that provide the importable `meow` package.
find_existing_checkout() {
    existing_python=''
    existing_checkout=''
    for candidate in $python_candidates; do
        is_supported_python "$candidate" || continue
        checkout=$(
            "$candidate" -c 'from pathlib import Path; import meow; package=Path(meow.__file__).resolve(); print(next((str(root) for root in (package.parents[2], package.parents[3]) if (root/".git").exists() and (root/"skills").is_dir()), ""))' \
                2>/dev/null | head -n 1
        ) || true
        if [ -n "$checkout" ]; then
            existing_python=$candidate
            existing_checkout=$checkout
            return 0
        fi
    done
    return 1
}

# Exit status: 0 installed < latest, 1 installed >= latest, 2 not comparable.
version_is_older() {
    awk -v installed="$1" -v latest="$2" '
        BEGIN {
            split(installed, left, /\./)
            split(latest, right, /\./)
            for (i = 1; i <= 3; i++) {
                if (left[i] == "") left[i] = 0
                if (right[i] == "") right[i] = 0
                if (left[i] !~ /^[0-9]+$/ || right[i] !~ /^[0-9]+$/) exit 2
                if ((left[i] + 0) < (right[i] + 0)) exit 0
                if ((left[i] + 0) > (right[i] + 0)) exit 1
            }
            exit 1
        }
    '
}

# Prints the version declared in origin/main's pyproject.toml, if reachable.
fetch_origin_version() {
    command -v curl >/dev/null 2>&1 || return 0
    curl -fsSL --max-time 10 "$origin_pyproject_url" 2>/dev/null \
        | sed -nE "s/^[[:space:]]*version[[:space:]]*=[[:space:]]*[\"']([^\"']+)[\"'].*/\1/p" \
        | head -n 1
}

update_existing_checkout() {
    printf '%s\n' "Updating MEOW from existing checkout: $existing_checkout"
    command -v git >/dev/null 2>&1 \
        || die 'Git is required to update the existing MEOW checkout.'
    run_with_spinner 'Pulling MEOW source' git -C "$existing_checkout" pull --ff-only origin main \
        || die 'MEOW source update failed.'
    run_with_spinner 'Installing editable MEOW update' "$existing_python" -m pip install -e "$existing_checkout" \
        || die 'Editable MEOW update failed.'
    printf '%s\n' 'MEOW updated successfully.'
}

# Handles a MEOW that is already installed: either a `meow` command on PATH or
# an importable `meow` package. Returns 1 when there is neither.
use_existing_meow() {
    existing_meow=$(command -v meow 2>/dev/null || true)
    find_existing_checkout || true
    [ -n "$existing_meow" ] || [ -n "$existing_checkout" ] || return 1

    if [ -n "$existing_meow" ]; then
        existing_location=$existing_meow
        installed_version=$(meow --version 2>/dev/null \
            | sed -nE 's/^[^0-9]*([0-9]+(\.[0-9]+)+).*/\1/p' \
            | head -n 1)
    else
        existing_location=$existing_checkout
        installed_version=$("$existing_python" -c "import importlib.metadata as m; print(m.version('meow'))" 2>/dev/null \
            | head -n 1)
    fi
    origin_version=$(fetch_origin_version)

    comparison=unknown
    if [ -n "$installed_version" ] && [ -n "$origin_version" ]; then
        comparison_status=0
        version_is_older "$installed_version" "$origin_version" || comparison_status=$?
        case $comparison_status in
            0) comparison=older ;;
            1) comparison=current ;;
        esac
    fi

    case $comparison in
        older)
            printf '%s\n' \
                "Warning: installed MEOW $installed_version is older than origin/main $origin_version; using the existing installation without reinstalling it." \
                >&2
            ;;
        current)
            printf '%s\n' "MEOW $installed_version is up to date $(printf '\342\234\223')"
            ;;
        *)
            printf '%s\n' \
                "MEOW is already installed at $existing_location, but its version could not be compared with origin/main; using the existing installation without reinstalling it." \
                >&2
            ;;
    esac

    if [ -z "$existing_checkout" ]; then
        printf '%s\n' \
            "Warning: could not locate the existing MEOW checkout; plugin registration and project onboarding were skipped." \
            >&2
        printf '%s\n' 'To update MEOW manually, run:'
        manual_python=$(select_python) || manual_python='<python>'
        printf '  %s -m pip install --upgrade git+https://github.com/maya8237/MEOW.git\n' "$manual_python"
        return 0
    fi

    if [ "$comparison" != current ]; then
        ask 'Update MEOW now? [y/N]: '
        case $reply in
            y|Y|yes|YES)
                update_existing_checkout
                ;;
            *)
                printf '%s\n' 'MEOW was not updated. To update it manually, run:'
                printf '  git -C "%s" pull --ff-only origin main && %s -m pip install -e "%s"\n' \
                    "$existing_checkout" "$existing_python" "$existing_checkout"
                ;;
        esac
    fi

    printf '%s\n' "Configuring Claude and onboarding projects using existing MEOW checkout: $existing_checkout"
    "$existing_python" -m meow.installer --repo-dir "$existing_checkout" \
        || die 'MEOW post-install setup failed.'
    return 0
}

install_fresh_meow() {
    ask_path "Parent directory for MEOW [$default_parent]"
    destination=${reply:-$default_parent}
    case $destination in
        '~') destination=$HOME ;;
        '~/'*) destination=$HOME/${destination#'~/'} ;;
    esac
    destination=${destination%/}

    case $destination in
        */MEOW) clone_path=$destination ;;
        *) clone_path=$destination/MEOW ;;
    esac

    command -v git >/dev/null 2>&1 || die 'Git is required. Install Git and run this installer again.'
    python_command=$(select_python) \
        || die 'MEOW requires system Python 3.12 or newer. Install it and run this installer again.'

    if [ -e "$clone_path" ]; then
        [ -e "$clone_path/.git" ] || die "Refusing to overwrite existing non-MEOW directory: $clone_path"
        printf 'Using existing MEOW checkout: %s\n' "$clone_path"
    else
        mkdir -p "$(dirname -- "$clone_path")"
        run_with_spinner "Cloning MEOW over SSH into $clone_path" git clone "$repository_url" "$clone_path" \
            || die 'SSH clone failed. Check that GitHub SSH authentication works with: ssh -T git@github.com'
    fi

    run_with_spinner "Installing MEOW with $python_command -m pip install -e ..." "$python_command" -m pip install -e "$clone_path" \
        || die 'Editable MEOW installation failed.'

    printf '%s\n' 'Configuring Claude and onboarding projects...'
    "$python_command" -m meow.installer --repo-dir "$clone_path" \
        || die 'MEOW post-install setup failed.'
}

show_banner
use_existing_meow || install_fresh_meow
printf '\033[32mDone!\033[0m\n'
