#!/usr/bin/env sh
# curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
set -eu

non_interactive=false
setup=true
bun=false
install_missing=false
mcp=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --non-interactive) non_interactive=true ;;
        --setup) setup=true ;;
        --no-setup) setup=false ;;
        --bun) bun=true ;;
        --install-missing) install_missing=true ;;
        --mcp)
            [ "$#" -ge 2 ] || { printf 'Missing value for --mcp\n' >&2; exit 2; }
            mcp=$2; shift ;;
        --mcp=*) mcp=${1#--mcp=} ;;
        -h|--help)
            printf '%s\n' 'Usage: sh install.sh [--non-interactive] [--no-setup] [--mcp jira,gitlab] [--install-missing] [--bun]' 'With a terminal, asks about MCP servers, the Bun shared store, and projects after installing.' '--no-setup or --non-interactive skips those questions; so does having no terminal.' '--mcp registers user-scope MCP servers from their credential environment variables.' '--install-missing pip-installs a missing Jira server (mcp-atlassian) for --mcp.' '--bun configures the Bun shared dependency store and migrates registered repos.'
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

cat <<'MEOW'

       /\_/\
      ( o.o )   meow
       > ^ <
      /     \
     (       )__
      \_____/___)

MEOW

root="$HOME/.meow"
case "$(uname -s)" in
    Linux) ;;
    *) printf '%s\n' 'Unsupported OS. MEOW supports Linux (install.sh) and Windows (install.ps1).' >&2; exit 1 ;;
esac

mkdir -p "$root/versions"
temporary=$(mktemp -d "$root/versions/.install-XXXXXXXX")
trap 'rm -rf "$temporary"' 0
trap 'exit 130' HUP INT TERM

# Newest Python 3.12+ on PATH (or MEOW_PYTHON); venvs resolve to their base.
printf '%s\n' 'Locating Python 3.12+...'
probe='import os,sys;v=sys.version_info;print(v[0]*10000+v[1]*100+v[2],os.path.realpath(sys._base_executable))'
{
    if [ -n "${MEOW_PYTHON:-}" ]; then
        printf '%s\n' "$MEOW_PYTHON"
    else
        printf '%s\n' "$PATH" | tr ':' '\n' | while IFS= read -r dir; do
            for file in "$dir"/python3 "$dir"/python3.[0-9]* "$dir"/python; do
                if [ -n "$dir" ] && [ -f "$file" ] && [ -x "$file" ]; then printf '%s\n' "$file"; fi
            done
        done
    fi
} >"$temporary/candidates"
python=''
best=0
while IFS= read -r candidate; do
    found=$("$candidate" -c "$probe" 2>/dev/null) || continue
    key=${found%% *}
    case "$key" in ''|*[!0-9]*) continue ;; esac
    if [ "$key" -ge 31200 ] && [ "$key" -gt "$best" ]; then
        best=$key
        python=${found#* }
    fi
done <"$temporary/candidates"
if [ -z "$python" ]; then
    printf '%s\n' 'MEOW needs Python 3.12 or newer. Install it (brew install python, apt install python3) or set MEOW_PYTHON.' >&2
    exit 1
fi

printf 'Using %s\nFetching the latest release...\n' "$python"
# Only the publishing workflow advances this manifest, after the wheel is on the index.
manifest_url=${MEOW_RELEASE_MANIFEST_URL:-https://raw.githubusercontent.com/maya8237/MEOW/release-channel/latest.json}
manifest=$(curl -fsSL --retry 3 "$manifest_url")
tag=$(printf '%s\n' "$manifest" | sed -nE 's/^[[:space:]]*"tag_name"[[:space:]]*:[[:space:]]*"([0-9]+\.[0-9]+\.[0-9]+)"[,]?[[:space:]]*$/\1/p')
digest=$(printf '%s\n' "$manifest" | sed -nE 's/^[[:space:]]*"wheel_sha256"[[:space:]]*:[[:space:]]*"([0-9a-f]{64})"[,]?[[:space:]]*$/\1/p')
if ! printf '%s\n' "$tag" | LC_ALL=C grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    printf '%s\n' 'No published stable MEOW release was found.' >&2
    exit 1
fi
if [ -z "$digest" ]; then
    printf '%s\n' "Release $tag has no wheel checksum; installation stopped." >&2
    exit 1
fi

# Install exactly the published wheel (pip checks its hash) into a staging venv,
# then let that release finish its own install from the staged package: pinned
# dependencies, launcher, plugin, and an atomic swap into versions/. An existing
# version is replaced, so rerunning also repairs one built by another Python.
destination="$root/versions/$tag"
bundle="$temporary/bundle"
mkdir "$bundle"
printf 'Creating environment and installing MEOW %s (pip output follows)...\n' "$tag"
printf 'meow-harness==%s --hash=sha256:%s\n' "$tag" "$digest" >"$temporary/wheel.txt"
# Use uv only if it is already installed; any uv failure falls back to pip.
if command -v uv >/dev/null 2>&1 \
    && uv venv --quiet --seed --python "$python" "$bundle/runtime" 2>/dev/null \
    && uv pip install --quiet --python "$bundle/runtime/bin/python3" \
        --no-deps --require-hashes --only-binary :all: -r "$temporary/wheel.txt" 2>/dev/null; then
    :
else
    rm -rf "$bundle/runtime"
    "$python" -m venv "$bundle/runtime" || {
        printf '%s\n' 'Python could not create a virtual environment (Debian/Ubuntu: install python3-venv).' >&2
        exit 1
    }
    "$bundle/runtime/bin/python3" -m pip install --disable-pip-version-check --no-input \
        --no-deps --require-hashes --only-binary :all: -r "$temporary/wheel.txt"
fi
purelib=$("$bundle/runtime/bin/python3" -c "import sysconfig;print(sysconfig.get_path('purelib'))")
printf '%s\n' 'Installing pinned dependencies and launcher (this can take minutes)...'
PYTHONPATH=$purelib "$python" -m meow.installer.releases --finish "$destination" --bundle "$bundle"

extra=
[ -z "$mcp" ] || extra="--mcp $mcp"
[ "$bun" = false ] || extra="$extra --bun"
[ "$install_missing" = false ] || extra="$extra --install-missing"
if [ "$setup" = false ] || [ "$non_interactive" = true ]; then
    "$destination/runtime/bin/python3" -m meow.installer.releases --activate "$destination" $extra
elif [ -r /dev/tty ] && (: </dev/tty) 2>/dev/null; then
    "$destination/runtime/bin/python3" -m meow.installer.releases --activate "$destination" --setup $extra </dev/tty
else
    "$destination/runtime/bin/python3" -m meow.installer.releases --activate "$destination" $extra
    printf '%s\n' 'No terminal available; skipped optional setup. Run `claude /meow:onboard` in a project, or rerun the installer in a terminal.'
fi
printf '\nMEOW is ready. Open a new terminal, or run:\n  export PATH="$HOME/.meow/bin:$PATH"\n'
printf '%s\n' 'Restart Claude Code to load /meow:* skills. Update later with meow -u.'
