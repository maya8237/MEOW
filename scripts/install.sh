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
setup_python=''

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

# Prints how version $1 compares with $2: older, equal, newer, or unknown.
compare_versions() {
    awk -v installed="$1" -v latest="$2" '
        BEGIN {
            split(installed, left, /\./)
            split(latest, right, /\./)
            for (i = 1; i <= 3; i++) {
                if (left[i] == "") left[i] = 0
                if (right[i] == "") right[i] = 0
                if (left[i] !~ /^[0-9]+$/ || right[i] !~ /^[0-9]+$/) { print "unknown"; exit }
                if ((left[i] + 0) < (right[i] + 0)) { print "older"; exit }
                if ((left[i] + 0) > (right[i] + 0)) { print "newer"; exit }
            }
            print "equal"
        }
    '
}

# Prints the version declared in the pyproject.toml of checkout $1.
checkout_version() {
    sed -nE "s/^[[:space:]]*version[[:space:]]*=[[:space:]]*[\"']([^\"']+)[\"'].*/\1/p" \
        "$1/pyproject.toml" 2>/dev/null | head -n 1
}

# Prints the version of the installed MEOW package (its pip metadata).
package_version() {
    "$existing_python" -c "import importlib.metadata as m; print(m.version('meow'))" 2>/dev/null \
        | head -n 1
}

# Prints the version declared in origin/main's pyproject.toml, if reachable.
fetch_origin_version() {
    command -v curl >/dev/null 2>&1 || return 0
    curl -fsSL --max-time 10 "$origin_pyproject_url" 2>/dev/null \
        | sed -nE "s/^[[:space:]]*version[[:space:]]*=[[:space:]]*[\"']([^\"']+)[\"'].*/\1/p" \
        | head -n 1
}

# Runs the interactive post-install setup and remembers the interpreter so the
# closing guidance can be shown after "Done!".
run_setup() {
    setup_python=$1
    setup_status=0
    "$1" -m meow.installer --repo-dir "$2" || setup_status=$?
    case $setup_status in
        0) ;;
        130) failure_reported=1; exit 130 ;; # cancelled with Ctrl+C
        *) die 'MEOW post-install setup failed.' ;;
    esac
}

# Reinstalls the checkout in editable mode so the package matches its source.
reinstall_existing_checkout() {
    run_with_spinner 'Installing editable MEOW update' "$existing_python" -m pip install -e "$existing_checkout" \
        || die 'Editable MEOW update failed.'
}

# Pulls the checkout, reinstalls it, then checks the result instead of trusting
# the commands' exit codes alone.
update_existing_checkout() {
    printf '%s\n' "Updating MEOW from existing checkout: $existing_checkout"
    command -v git >/dev/null 2>&1 \
        || die 'Git is required to update the existing MEOW checkout.'
    if [ "${1:-pull}" = reset ]; then
        # Moving to an older origin/main cannot fast-forward, so reset to it.
        [ -z "$(git -C "$existing_checkout" status --porcelain 2>/dev/null)" ] \
            || die 'The MEOW checkout has uncommitted changes; commit or stash them, then run the installer again.'
        run_with_spinner 'Fetching origin/main' git -C "$existing_checkout" fetch origin main \
            || die 'MEOW source update failed.'
        run_with_spinner 'Resetting MEOW source to origin/main' \
            git -C "$existing_checkout" reset --hard FETCH_HEAD \
            || die 'MEOW source update failed.'
    else
        run_with_spinner 'Pulling MEOW source' git -C "$existing_checkout" pull --ff-only origin main \
            || die 'MEOW source update failed.'
    fi
    reinstall_existing_checkout

    updated_source=$(checkout_version "$existing_checkout")
    updated_package=$(package_version)
    printf '%s\n' "MEOW updated successfully${updated_source:+ (now $updated_source)}."
    if [ -n "$origin_version" ] && [ -n "$updated_source" ] \
        && [ "$(compare_versions "$updated_source" "$origin_version")" = older ]; then
        printf '%s\n' \
            "Warning: the checkout is still at $updated_source but origin/main has $origin_version; check that 'git -C \"$existing_checkout\" status' is on main." \
            >&2
    fi
    if [ -n "$updated_source" ] && [ -n "$updated_package" ] && [ "$updated_source" != "$updated_package" ]; then
        printf '%s\n' \
            "Warning: the installed package ($updated_package) still differs from the checkout ($updated_source)." \
            >&2
    fi
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
        installed_version=$(package_version)
    fi
    origin_version=$(fetch_origin_version)

    # The checkout's own pyproject.toml is the source of truth for an editable
    # install; the package metadata can lag behind it after a pull or checkout.
    source_version=''
    [ -z "$existing_checkout" ] || source_version=$(checkout_version "$existing_checkout")
    current_version=${source_version:-$installed_version}

    package_out_of_sync=0
    if [ -n "$source_version" ] && [ -n "$installed_version" ] \
        && [ "$source_version" != "$installed_version" ]; then
        package_out_of_sync=1
        printf '%s\n' \
            "Warning: the installed MEOW package ($installed_version) does not match the checkout ($source_version)." \
            >&2
    fi

    comparison=unknown
    if [ -n "$current_version" ] && [ -n "$origin_version" ]; then
        comparison=$(compare_versions "$current_version" "$origin_version")
    fi

    case $comparison in
        older)
            printf '%s\n' \
                "Warning: installed MEOW $current_version is older than origin/main $origin_version." \
                >&2
            ;;
        equal)
            printf '%s\n' "MEOW $current_version is already up to date :)"
            ;;
        newer)
            printf '%s\n' "MEOW $current_version is newer than origin/main $origin_version."
            ;;
        *)
            printf '%s\n' \
                "MEOW is already installed at $existing_location, but its version could not be compared with origin/main." \
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

    if [ "$comparison" = older ] || [ "$comparison" = unknown ]; then
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
    elif [ "$comparison" = newer ]; then
        ask "Switch to the origin/main version ($origin_version), discarding local commits? [y/N]: "
        case $reply in
            y|Y|yes|YES)
                update_existing_checkout reset
                ;;
            *)
                printf '%s\n' "Keeping MEOW $current_version."
                ;;
        esac
    elif [ "$package_out_of_sync" -eq 1 ]; then
        ask 'Reinstall MEOW from the checkout now? [y/N]: '
        case $reply in
            y|Y|yes|YES)
                reinstall_existing_checkout
                printf '%s\n' 'MEOW reinstalled.'
                ;;
            *)
                printf '%s\n' 'MEOW was not reinstalled. To fix it manually, run:'
                printf '  %s -m pip install -e "%s"\n' "$existing_python" "$existing_checkout"
                ;;
        esac
    fi

    printf '%s\n' "Configuring Claude and onboarding projects using existing MEOW checkout: $existing_checkout"
    run_setup "$existing_python" "$existing_checkout"
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

    if [ -e "$destination/.git" ] && [ -d "$destination/skills" ]; then
        clone_path=$destination
    else
        case $destination in
            */MEOW) clone_path=$destination ;;
            *) clone_path=$destination/MEOW ;;
        esac
    fi

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
    run_setup "$python_command" "$clone_path"
}

show_banner
use_existing_meow || install_fresh_meow
printf '\033[32mDone!\033[0m\n'
[ -z "$setup_python" ] || "$setup_python" -m meow.installer --next-steps || true
