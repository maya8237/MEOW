#!/usr/bin/env sh
# curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
set -eu

non_interactive=false
setup=true
bun=false
install_missing=false
beta=false
mcp=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --non-interactive) non_interactive=true ;;
        --setup) setup=true ;;
        --no-setup) setup=false ;;
        --bun) bun=true ;;
        --install-missing) install_missing=true ;;
        --beta) beta=true ;;
        --mcp)
            [ "$#" -ge 2 ] || { printf 'Missing value for --mcp\n' >&2; exit 2; }
            mcp=$2; shift ;;
        --mcp=*) mcp=${1#--mcp=} ;;
        -h|--help)
            printf '%s\n' 'Usage: sh install.sh [--non-interactive] [--no-setup] [--beta] [--mcp atlassian,gitlab] [--install-missing] [--bun]' 'With a terminal, asks about MCP servers, the Bun shared store, and projects after installing.' '--no-setup or --non-interactive skips those questions; so does having no terminal.' '--beta also considers beta releases; by default the newest stable PyPI release is installed.' '--mcp registers user-scope MCP servers from their credential environment variables.' '--install-missing pip-installs a missing Jira server (mcp-atlassian) for --mcp.' '--bun configures the Bun shared dependency store and migrates registered repos.'
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

printf 'Using %s\n' "$python"
printf '%s\n' 'Fetching the latest release from PyPI...'
release=
if pypi_json=$(curl -fsSL --retry 3 \
    -H 'Accept: application/vnd.pypi.simple.v1+json' \
    "${MEOW_PYPI_INDEX_URL:-https://pypi.org/simple/meow-harness/}"); then
    release=$(printf '%s' "$pypi_json" | MEOW_BETA=$beta "$python" -c '
import json
import os
import re
import sys

data = json.load(sys.stdin)
beta = os.environ["MEOW_BETA"] == "true"
candidates = []
for file in data.get("files", []):
    filename = file.get("filename")
    match = re.fullmatch(r"meow[-_]harness-(\d+)\.(\d+)\.(\d+)(?:b(\d+))?-[^/]+\.whl", filename or "")
    if not match or file.get("yanked"):
        continue
    major, minor, patch, pre = match.groups()
    # A beta sorts before the stable release it precedes.
    if pre and not beta:
        continue
    tag = f"{major}.{minor}.{patch}" + (f"b{pre}" if pre else "")
    digest = file.get("hashes", {}).get("sha256")
    if re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        key = (int(major), int(minor), int(patch), 0 if pre else 1, int(pre or 0))
        candidates.append((key, tag, digest))
if not candidates:
    raise SystemExit("No published MEOW wheel was found on PyPI.")
_, tag, digest = max(candidates)
print(tag, digest)
    ' 2>/dev/null) || release=
fi
if [ -z "$release" ] && [ "$beta" = false ] && [ -z "${MEOW_PYPI_INDEX_URL:-}" ]; then
    printf '%s\n' 'PyPI Index API unavailable; trying the legacy JSON API...'
    pypi_json=$(curl -fsSL --retry 3 'https://pypi.org/pypi/meow-harness/json')
    release=$(printf '%s' "$pypi_json" | "$python" -c '
import json
import re
import sys

data = json.load(sys.stdin)
info = data.get("info", {})
tag = info.get("version")
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", tag or ""):
    raise SystemExit("PyPI legacy JSON has no stable project version")
for file in data.get("urls", []):
    if file.get("packagetype") != "bdist_wheel" or file.get("yanked"):
        continue
    digest = file.get("digests", {}).get("sha256")
    if re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        print(tag, digest)
        break
else:
    raise SystemExit("PyPI legacy JSON has no stable wheel with a SHA-256 checksum")
')
fi
if [ -z "$release" ]; then
    printf '%s\n' 'No published MEOW release was found on PyPI.' >&2
    exit 1
fi
tag=${release%% *}
digest=${release#* }

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
[ "$bun" = false ] || extra="$extra --bun"
if [ "$setup" = false ] || [ "$non_interactive" = true ]; then
    "$destination/runtime/bin/python3" -m meow.installer.releases --activate "$destination" $extra
elif [ -r /dev/tty ] && (: </dev/tty) 2>/dev/null; then
    "$destination/runtime/bin/python3" -m meow.installer.releases --activate "$destination" --setup $extra </dev/tty
else
    "$destination/runtime/bin/python3" -m meow.installer.releases --activate "$destination" $extra
    printf '%s\n' 'No terminal available; skipped optional setup. Run `claude /meow:onboard` in a project, or rerun the installer in a terminal.'
fi
if [ -n "$mcp" ]; then
    if [ "$install_missing" = true ]; then
        "$destination/runtime/bin/python3" -m meow mcp "$mcp" --install-missing
    else
        "$destination/runtime/bin/python3" -m meow mcp "$mcp"
    fi
fi
printf '\nMEOW is ready. Open a new terminal, or run:\n  export PATH="$HOME/.meow/bin:$PATH"\n'
printf '%s\n' 'Restart Claude Code to load /meow:* skills. Update later with meow -u.'
