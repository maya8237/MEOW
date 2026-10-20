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

python=${MEOW_PYTHON:-python3}
if ! "$python" -c 'import sys;sys.exit(sys.version_info < (3,12))'; then
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
    temporary=$(mktemp)
    curl -fsSL --retry 3 https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.py -o "$temporary"
    script=$temporary
fi
if [ -r /dev/tty ] && (: </dev/tty) 2>/dev/null; then
    "$python" "$script" </dev/tty
else
    "$python" "$script" </dev/null
fi
