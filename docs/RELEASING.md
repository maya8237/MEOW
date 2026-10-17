# Releasing MEOW

MEOW's installers and `meow --update` consume published stable GitHub Releases.
Commits to `main` do not update users. The first release using this installer
must be published before switching the public installer scripts on `main`.

1. Update the version in `pyproject.toml`, `.claude-plugin/plugin.json`, and
   `.claude-plugin/marketplace.json` together.
2. If dependencies changed, regenerate the pinned runtime requirements:
   `uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes --output-file requirements-release.txt`.
3. Run `python -m pytest -q`, `ruff check .`, and `ruff format --check .`.
4. Push a `vX.Y.Z` tag matching the package version. For example, for version
   `0.1.4`, run `git tag v0.1.4` followed by `git push origin v0.1.4`.

The Release workflow builds Windows x64, Linux x64/ARM64, and macOS x64/ARM64
archives, verifies that the tag and all plugin versions agree, and runs the
streamed installer against a local release server on each platform. Only after
all builds and smoke tests pass does it publish a GitHub Release with generated
notes, all archives, and per-archive `.sha256` files. It then advances
`latest.json` on the `release-channel` branch. Installers and update checks
read this manifest through `raw.githubusercontent.com`; they do not require
`api.github.com`. The channel points only at a published release, never a
source commit. A failed build leaves no
incomplete public release. Manual workflow runs build and test without
publishing. PRs that affect packaging also exercise this workflow.

Publication is serialized and can be rerun after a channel-push failure.
Archives and checksums are uploaded to a draft release and published together.
Failed draft uploads can be replaced on retry; published assets are retained.
An older tag cannot replace a newer tag in the release channel.

To reproduce a platform's bundle locally, install uv and Python 3.12+, then run
`python scripts/build_release.py` and `python scripts/smoke_release.py`.
Builds install the wheel and hash-pinned dependencies into an isolated,
relocatable CPython runtime obtained through uv. They do not install MEOW into
the user's system Python. Windows builds compile the small stable launcher
using the .NET Framework compiler included with Windows; Unix launchers use
`sh`. Dependencies keep their licenses in the bundled runtime.

The versioned runtimes intentionally remain after updates so active workers
and OS schedules can still launch their original interpreter. Keep those
versions until their jobs have finished or their schedules are recreated.
