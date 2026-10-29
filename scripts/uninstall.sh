#!/usr/bin/env sh
# curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.sh | sh
set -eu

cat <<'MEOW'

       /\_/\
      ( o.o )   meow
       > ^ <
      /     \
     (       )__
      \_____/___)

MEOW

# Newest Python 3.12+ on PATH (or MEOW_PYTHON), found as install.sh does;
# venvs resolve to their base interpreter.
probe='import os,sys;v=sys.version_info;print(v[0]*10000+v[1]*100+v[2],os.path.realpath(sys._base_executable))'
candidates=$(
    if [ -n "${MEOW_PYTHON:-}" ]; then
        printf '%s\n' "$MEOW_PYTHON"
    else
        printf '%s\n' "$PATH" | tr ':' '\n' | while IFS= read -r dir; do
            for file in "$dir"/python3 "$dir"/python3.[0-9]* "$dir"/python; do
                if [ -n "$dir" ] && [ -f "$file" ] && [ -x "$file" ]; then printf '%s\n' "$file"; fi
            done
        done
    fi
)
python=''
best=0
while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
    found=$("$candidate" -c "$probe" 2>/dev/null) || continue
    key=${found%% *}
    case "$key" in ''|*[!0-9]*) continue ;; esac
    if [ "$key" -ge 31200 ] && [ "$key" -gt "$best" ]; then
        best=$key
        python=${found#* }
    fi
done <<CANDIDATES
$candidates
CANDIDATES
if [ -z "$python" ]; then
    printf '%s\n' 'Python 3.12+ is required; set MEOW_PYTHON to its path.' >&2
    exit 1
fi

# A checkout uses its sibling helper; a streamed script downloads it temporarily.
script=''
case "$0" in
    uninstall.sh|*/uninstall.sh) script="$(dirname "$0")/uninstall.py" ;;
esac
temporary=''
trap '[ -z "$temporary" ] || rm -f "$temporary"' 0
trap 'exit 130' HUP INT TERM
if [ -z "$script" ] || [ ! -f "$script" ]; then
    printf '%s\n' 'Downloading the uninstaller...'
    temporary=$(mktemp)
    curl -fsSL --retry 3 https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.py -o "$temporary"
    script=$temporary
fi
if [ -r /dev/tty ] && (: </dev/tty) 2>/dev/null; then
    "$python" "$script" "$@" </dev/tty
else
    "$python" "$script" "$@" </dev/null
fi
