#!/usr/bin/env sh
# Streamable MEOW installer. Run with:
# curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/install.sh | sh

set -eu

repository_url='git@github.com:maya8237/MEOW.git'
default_parent=${HOME:-"$(pwd)"}

die() {
    printf '%s\n' "MEOW installer: $*" >&2
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
    printf 'Cloning MEOW over SSH into %s\n' "$clone_path"
    git clone "$repository_url" "$clone_path" \
        || die 'SSH clone failed. Check that GitHub SSH authentication works with: ssh -T git@github.com'
fi

python_command=$(select_python) \
    || die 'MEOW requires system Python 3.12 or newer. Install it and run this installer again.'

printf 'Installing MEOW with %s -m pip install -e ...\n' "$python_command"
"$python_command" -m pip install -e "$clone_path" \
    || die 'Editable MEOW installation failed.'

printf '%s\n' 'Configuring Claude and onboarding projects...'
"$python_command" -m meow.installer --repo-dir "$clone_path" \
    || die 'MEOW post-install setup failed.'
