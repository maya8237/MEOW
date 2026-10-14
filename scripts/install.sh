#!/usr/bin/env sh
# Streamable MEOW installer. Run with:
# curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
#
# With an existing `meow` on PATH it offers to update that checkout and re-runs
# the post-install setup; otherwise it clones MEOW over HTTPS, installs it
# editable, and runs the post-install setup.

set -eu

repository_url='https://github.com/maya8237/MEOW.git'
origin_pyproject_url='https://raw.githubusercontent.com/maya8237/MEOW/main/pyproject.toml'
default_parent=${HOME:-"$(pwd)"}
python_candidates='python3.15 python3.14 python3.13 python3.12 python3 python'
python_probe_timeout=10
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

is_supported_python() (
    command -v "$1" >/dev/null 2>&1 || return 1
    # A launcher can exist on PATH yet crash or hang. Check Python and pip
    # before using that interpreter for installation or checkout discovery.
    probe_output=$(mktemp) || return 1
    trap 'rm -f "$probe_output"' 0
    "$1" -c 'import sys, runpy; sys.exit(2) if sys.version_info < (3, 12) else None; print("%d.%d.%d" % sys.version_info[:3], flush=True); sys.argv = ["pip", "--version"]; runpy.run_module("pip", run_name="__main__")' </dev/null >"$probe_output" 2>/dev/null &
    probe_pid=$!
    (sleep "$python_probe_timeout"; kill -KILL "$probe_pid" 2>/dev/null) >/dev/null 2>&1 &
    timer_pid=$!
    if wait "$probe_pid" 2>/dev/null; then probe_status=0; else probe_status=$?; fi
    kill "$timer_pid" 2>/dev/null || true
    wait "$timer_pid" 2>/dev/null || true
    if [ "$probe_status" -ne 0 ]; then
        printf 'Warning: %s failed the Python health check (Python 3.12+ and pip required; %ss timeout). Trying another interpreter.\n' "$1" "$python_probe_timeout" >&2
        return 1
    fi
    sed -n '1p' "$probe_output"
)

python_paths() {
    # Include every matching executable on PATH, not just the first alias.
    for candidate in $python_candidates; do command -v "$candidate" 2>/dev/null || true; done
    (
        IFS=:
        for directory in $PATH; do
            for candidate in "${directory:-.}"/python*; do
                [ -f "$candidate" ] && [ -x "$candidate" ] || continue
                case ${candidate##*/} in
                    python|python3|python3.[0-9]|python3.[0-9][0-9]|python3.[0-9]t|python3.[0-9][0-9]t|python.exe|python3.exe|python3.[0-9].exe|python3.[0-9][0-9].exe) printf '%s\n' "$candidate" ;;
                esac
            done
        done
    )
}

select_python() {
    python_paths | awk '!seen[$0]++' | (
        best_python=''
        best_score=0
        while IFS= read -r candidate; do
            version=$(is_supported_python "$candidate") || continue
            score=$(printf '%s\n' "$version" | awk -F. 'NF == 3 {print $1*1000000+$2*1000+$3}')
            [ -n "$score" ] || continue
            if [ "$score" -gt "$best_score" ]; then best_python=$candidate; best_score=$score; fi
        done
        [ -n "$best_python" ] || return 1
        printf '%s\n' "$best_python"
    )
}

# Sets existing_python/existing_checkout to the interpreter and git checkout
# that provide the importable `meow` package.
find_existing_checkout() {
    existing_python=''
    existing_checkout=''
    candidates=$(python_paths | awk '!seen[$0]++')
    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        is_supported_python "$candidate" >/dev/null || continue
        checkout=$(
            "$candidate" -c 'from pathlib import Path; import meow; package=Path(meow.__file__).resolve(); print(next((str(root) for root in (package.parents[2], package.parents[3]) if (root/".git").exists() and (root/"skills").is_dir()), ""))' \
                2>/dev/null | head -n 1
        ) || true
        if [ -n "$checkout" ]; then
            existing_python=$candidate
            existing_checkout=$checkout
            return 0
        fi
    done <<EOF
$candidates
EOF
    return 1
}

remove_previous_meow() {
    distribution_query='import os, importlib.metadata as m; print(os.path.normcase(os.path.realpath(m.distribution("meow").locate_file(""))))'
    candidates=$(python_paths | awk '!seen[$0]++')
    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        if ! is_supported_python "$candidate" >/dev/null; then
            printf 'Warning: cannot inspect or uninstall previous MEOW from broken Python: %s\n' "$candidate" >&2
            continue
        fi
        candidate_distribution=$("$candidate" -c "$distribution_query" 2>/dev/null) || continue
        if ! run_with_spinner "Removing previous MEOW from $candidate" "$candidate" -m pip uninstall -y meow; then
            printf 'Warning: could not uninstall previous MEOW from %s\n' "$candidate" >&2
        fi
    done <<EOF
$candidates
EOF
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
    if has_tty; then
        "$1" -m meow.installer --repo-dir "$2" </dev/tty || setup_status=$?
    else
        "$1" -m meow.installer --repo-dir "$2" || setup_status=$?
    fi
    case $setup_status in
        0) ;;
        130) failure_reported=1; exit 130 ;; # cancelled with Ctrl+C
        *) die 'MEOW post-install setup failed.' ;;
    esac
}

# Reinstalls the checkout in editable mode so the package matches its source.
run_maintenance() {
    # Older checkouts do not yet contain the shared runtime. Fetch only that
    # stdlib helper, using the same HTTPS source as this streamed installer.
    maintenance_python=$1
    shift
    "$maintenance_python" -c 'import pathlib, sys, urllib.request; repo=sys.argv[1]; path=pathlib.Path(repo)/"src/meow/installer/maintenance.py"; source=path.read_bytes() if path.is_file() else urllib.request.urlopen("https://raw.githubusercontent.com/maya8237/MEOW/main/src/meow/installer/maintenance.py", timeout=10).read(); sys.argv=[str(path), "--repo-dir", repo, *sys.argv[2:]]; exec(compile(source, str(path), "exec"), {"__name__": "__main__"})' "$@"
}

reinstall_existing_checkout() {
    remove_previous_meow
    run_with_spinner 'Installing editable MEOW update' run_maintenance "$existing_python" "$existing_checkout" --install \
        || die 'Editable MEOW update failed.'
    installed_current=1
}

# Pulls the checkout, reinstalls it, then checks the result instead of trusting
# the commands' exit codes alone.
update_existing_checkout() {
    printf '%s\n' "Updating MEOW from existing checkout: $existing_checkout"
    command -v git >/dev/null 2>&1 \
        || die 'Git is required to update the existing MEOW checkout.'
    if [ "${1:-pull}" = reset ]; then
        run_with_spinner 'Resetting MEOW source to origin/main' run_maintenance "$existing_python" "$existing_checkout" --update --reset \
            || die 'MEOW source update failed.'
    else
        run_with_spinner 'Pulling MEOW source' run_maintenance "$existing_python" "$existing_checkout" --update \
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
        installed_version=''
        [ -z "$existing_python" ] || installed_version=$(package_version)
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
            "Warning: could not locate the existing MEOW checkout. Installing a fresh checkout with the newest healthy Python." \
            >&2
        return 1
    fi

    newest_python=$(select_python) \
        || die 'No working Python 3.12+ interpreter with pip found. Install or repair Python and run this installer again.'
    previous_python=$existing_python
    existing_python=$newest_python
    installed_current=0
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

    if [ "$previous_python" != "$existing_python" ] && [ "$installed_current" -eq 0 ]; then
        reinstall_existing_checkout
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
        || die 'No working Python 3.12+ interpreter with pip found. Install or repair Python and run this installer again.'

    if [ -e "$clone_path" ]; then
        [ -e "$clone_path/.git" ] || die "Refusing to overwrite existing non-MEOW directory: $clone_path"
        printf 'Using existing MEOW checkout: %s\n' "$clone_path"
    else
        mkdir -p "$(dirname -- "$clone_path")"
        run_with_spinner "Cloning MEOW over HTTPS into $clone_path" git clone "$repository_url" "$clone_path" \
            || die 'HTTPS clone failed. Check your network connection and access to https://github.com/maya8237/MEOW.git'
    fi

    remove_previous_meow
    run_with_spinner "Installing MEOW with $python_command -m pip install -e ..." run_maintenance "$python_command" "$clone_path" --install \
        || die 'Editable MEOW installation failed.'

    printf '%s\n' 'Configuring Claude and onboarding projects...'
    run_setup "$python_command" "$clone_path"
}

show_banner
use_existing_meow || install_fresh_meow
printf '\033[32mDone!\033[0m\n'
[ -z "$setup_python" ] || "$setup_python" -m meow.installer --next-steps || true
