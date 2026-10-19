#!/usr/bin/env sh
# curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
set -eu

cat <<'MEOW'

       /\_/\
      ( o.o )   meow
       > ^ <
      /     \
     (       )__
      \_____/___)

MEOW

base=${MEOW_RELEASE_BASE_URL:-https://github.com/maya8237/MEOW/releases}
root="$HOME/.meow"
case "$(uname -s)" in
    Linux) system=linux ;;
    Darwin) system=macos ;;
    *) printf '%s\n' 'Unsupported OS. Use install.ps1 on Windows.' >&2; exit 1 ;;
esac
case "$(uname -m)" in
    x86_64|amd64) architecture=x86_64 ;;
    arm64|aarch64) architecture=arm64 ;;
    *) printf '%s\n' 'Unsupported CPU architecture.' >&2; exit 1 ;;
esac

# Only the publishing workflow advances this manifest, after all assets exist.
manifest_url=${MEOW_RELEASE_MANIFEST_URL:-https://raw.githubusercontent.com/maya8237/MEOW/release-channel/latest.json}
manifest=$(curl -fsSL --retry 3 "$manifest_url")
tag=$(printf '%s\n' "$manifest" | sed -nE 's/^[[:space:]]*"tag_name"[[:space:]]*:[[:space:]]*"(v[0-9]+\.[0-9]+\.[0-9]+)"[,]?[[:space:]]*$/\1/p')
if ! printf '%s\n' "$tag" | LC_ALL=C grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
    printf '%s\n' 'No published stable MEOW release was found.' >&2
    exit 1
fi
asset="meow-$system-$architecture.tar.gz"
destination="$root/versions/$tag"
mkdir -p "$root/versions"
temporary=$(mktemp -d "$root/versions/.install-XXXXXXXX")
trap 'rm -rf "$temporary"' 0
trap 'exit 130' HUP INT TERM

if [ ! -d "$destination" ]; then
    printf 'Downloading MEOW %s for %s/%s...\n' "$tag" "$system" "$architecture"
    curl -fsSL --retry 3 "$base/download/$tag/$asset" -o "$temporary/$asset"
    curl -fsSL --retry 3 "$base/download/$tag/$asset.sha256" -o "$temporary/$asset.sha256"
    if command -v sha256sum >/dev/null 2>&1; then
        (cd "$temporary" && sha256sum -c "$asset.sha256")
    else
        (cd "$temporary" && shasum -a 256 -c "$asset.sha256")
    fi
    mkdir "$temporary/release"
    tar -xzf "$temporary/$asset" -C "$temporary/release"
    mv "$temporary/release" "$destination"
fi

# The downloaded runtime supplies Python. No Git, pip, or system Python needed.
if [ -r /dev/tty ] && (: </dev/tty) 2>/dev/null; then
    "$destination/runtime/bin/python3" -m meow.installer.releases --activate "$destination" --setup </dev/tty
else
    "$destination/runtime/bin/python3" -m meow.installer.releases --activate "$destination" --setup </dev/null
fi
printf '\nMEOW is ready. Open a new terminal, or run:\n  export PATH="$HOME/.meow/bin:$PATH"\n'
printf '%s\n' 'Restart Claude Code to load /meow:* skills. Update later with meow -u.'
