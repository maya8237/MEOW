#!/usr/bin/env sh
# Streamable MEOW installer. Run with:
# curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh

set -eu

repository_url='git@github.com:maya8237/MEOW.git'
origin_pyproject_url='https://raw.githubusercontent.com/maya8237/MEOW/main/pyproject.toml'
default_parent=${HOME:-"$(pwd)"}
failure_reported=0

show_banner() {
    printf '\033[36m\n'
    printf '%s\n' '  +--------------------------------------+'
    printf '%s\n' '  |           MEOW INSTALLER              |'
    printf '%s\n' '  |  Management, Execution & Optimization |'
    printf '%s\n' '  +--------------------------------------+'
    printf '\033[0m\n'
}

run_with_spinner() (
    spinner_message=$1
    shift
    spinner_chars='|/-\'

    printf '  %s ' "$spinner_message"
    printf '\033[?25l'
    "$@" &
    spinner_pid=$!
    while kill -0 "$spinner_pid" 2>/dev/null; do
        spinner_char=${spinner_chars%"${spinner_chars#?}"}
        printf '\033[36m[%s]\033[0m' "$spinner_char"
        spinner_chars=${spinner_chars#?}$spinner_char
        sleep 0.1 || true
        printf '\b\b\b'
    done

    if wait "$spinner_pid"; then
        spinner_status=0
    else
        spinner_status=$?
    fi

    printf '\033[?25h\r\033[2K'
    if [ "$spinner_status" -eq 0 ]; then
        printf '\033[32m  [OK] %s\033[0m\n' "$spinner_message"
    else
        printf '\033[31m  [FAIL] %s\033[0m\n' "$spinner_message"
    fi
    exit "$spinner_status"
)

report_unexpected_failure() {
    status=$?
    if [ "$status" -ne 0 ] && [ "$failure_reported" -eq 0 ]; then
        printf '\033[31mFailed: installer exited with status %s.\033[0m\n' "$status" >&2
    fi
}

trap report_unexpected_failure 0

die() {
    failure_reported=1
    printf '\033[31mFailed: %s\033[0m\n' "$*" >&2
    exit 1
}

select_python() {
    for candidate in python3.15 python3.14 python3.13 python3.12 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

existing_python=''
existing_checkout=''

find_existing_checkout() {
    existing_python=''
    existing_checkout=''
    for candidate in python3.15 python3.14 python3.13 python3.12 python3 python; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi
        if ! "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
            continue
        fi
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

stop_for_existing_meow() {
    existing_meow=$(command -v meow 2>/dev/null || true)
    [ -n "$existing_meow" ] || return 1

    installed_version=$(meow --version 2>/dev/null \
        | sed -nE 's/^[^0-9]*([0-9]+(\.[0-9]+)+).*/\1/p' \
        | head -n 1)
    origin_version=''
    if command -v curl >/dev/null 2>&1; then
        origin_version=$(curl -fsSL --max-time 10 "$origin_pyproject_url" 2>/dev/null \
            | sed -nE "s/^[[:space:]]*version[[:space:]]*=[[:space:]]*[\"']([^\"']+)[\"'].*/\1/p" \
            | head -n 1)
    fi

    if [ -z "$installed_version" ] || [ -z "$origin_version" ]; then
        printf '%s\n' \
            "MEOW is already installed at $existing_meow, but its version could not be compared with origin/main; using the existing installation without reinstalling it." \
            >&2
    else
        if version_is_older "$installed_version" "$origin_version"; then
            printf '%s\n' \
                "Warning: installed MEOW $installed_version is older than origin/main $origin_version; using the existing installation without reinstalling it." \
                >&2
        else
            comparison_status=$?
            if [ "$comparison_status" -eq 2 ]; then
                printf '%s\n' \
                    "MEOW is already installed at $existing_meow, but its version could not be compared with origin/main; using the existing installation without reinstalling it." \
                    >&2
            else
                printf '%s\n' \
                    "MEOW is already installed at $existing_meow; using the existing installation without reinstalling it."
            fi
        fi
    fi

    if find_existing_checkout; then
        update_command="git -C \"$existing_checkout\" pull --ff-only origin main && $existing_python -m pip install -e \"$existing_checkout\""
        printf '%s' 'Update MEOW now? [y/N]: '
        update_answer=''
        IFS= read -r update_answer || true
        case "$update_answer" in
            y|Y|yes|YES)
                printf '%s\n' "Updating MEOW from existing checkout: $existing_checkout"
                command -v git >/dev/null 2>&1 \
                    || die 'Git is required to update the existing MEOW checkout.'
                run_with_spinner 'Pulling MEOW source' git -C "$existing_checkout" pull --ff-only origin main \
                    || die 'MEOW source update failed.'
                run_with_spinner 'Installing editable MEOW update' "$existing_python" -m pip install -e "$existing_checkout" \
                    || die 'Editable MEOW update failed.'
                printf '%s\n' 'MEOW updated successfully.'
                ;;
            *)
                printf '%s\n' 'MEOW was not updated. To update it manually, run:'
                printf '  %s\n' "$update_command"
                ;;
        esac
        printf '%s\n' "Configuring Claude and onboarding projects using existing MEOW checkout: $existing_checkout"
        "$existing_python" -m meow.installer --repo-dir "$existing_checkout" \
            || die 'MEOW post-install setup failed.'
    else
        printf '%s\n' \
            "Warning: could not locate the existing MEOW checkout; plugin registration and project onboarding were skipped." \
            >&2
        printf '%s\n' 'To update MEOW manually, run:'
        printf '  python3 -m pip install --upgrade git+https://github.com/maya8237/MEOW.git\n'
    fi
    return 0
}

show_banner

if stop_for_existing_meow; then
    printf '\033[32mDone!\033[0m\n'
    exit 0
fi

printf 'Parent directory for MEOW [%s]: ' "$default_parent"
IFS= read -r destination || true
destination=${destination:-$default_parent}
case $destination in
    '~') destination=$HOME ;;
    '~/'*) destination=$HOME/${destination#~/} ;;
esac

case $destination in
    */MEOW) clone_path=$destination ;;
    *) clone_path=$destination/MEOW ;;
esac

command -v git >/dev/null 2>&1 || die 'Git is required. Install Git and run this installer again.'

if [ -e "$clone_path" ]; then
    [ -e "$clone_path/.git" ] || die "Refusing to overwrite existing non-MEOW directory: $clone_path"
    printf 'Using existing MEOW checkout: %s\n' "$clone_path"
else
    mkdir -p "$destination"
    run_with_spinner "Cloning MEOW over SSH into $clone_path" git clone "$repository_url" "$clone_path" \
        || die 'SSH clone failed. Check that GitHub SSH authentication works with: ssh -T git@github.com'
fi

python_command=$(select_python) \
    || die 'MEOW requires system Python 3.12 or newer. Install it and run this installer again.'

run_with_spinner "Installing MEOW with $python_command -m pip install -e ..." "$python_command" -m pip install -e "$clone_path" \
    || die 'Editable MEOW installation failed.'

printf '%s\n' 'Configuring Claude and onboarding projects...'
"$python_command" -m meow.installer --repo-dir "$clone_path" \
    || die 'MEOW post-install setup failed.'

printf '\033[32mDone!\033[0m\n'
