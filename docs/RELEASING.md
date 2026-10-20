# Releasing MEOW

Commit and push the changes to `main`, then create and push a three-part tag:

```bash
git tag 0.1.6
git push origin 0.1.6
```

That is the release process. CI stamps the tag's version into the Python package
and both plugin manifests, runs tests and lint, installs the checkout through the
real installers on each platform, then publishes. No prefix, alias tag, or manual
version edits are needed. Commits to `main` alone do not update users.

MEOW is published to PyPI as `meow-harness` (the import package and `meow`
command are unchanged). The Release workflow's publish job builds the sdist and
wheel, uploads them with PyPI trusted publishing (no stored token), reads the
wheel's SHA-256 back from PyPI, creates the GitHub Release (notes only, no
attached files), and advances `latest.json` on the `release-channel` branch to
`{"tag_name", "wheel_sha256"}`. Installers and update checks read it through
`raw.githubusercontent.com`; they do not require `api.github.com`. The channel
points only at a version already on PyPI. Publication is serialized and can be
rerun after a later failure; an older tag cannot replace a newer one.

One-time setup: reserve `meow-harness` on pypi.org and add a trusted publisher
for this repository, workflow `release.yml`. PyPI versions cannot be replaced, so
fix a bad release with a new tag.

When dependencies change, regenerate the pinned runtime requirements before
committing:
`uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes --output-file requirements-release.txt`.
The wheel carries that file, the skills, plugin manifests, and launcher sources
under `meow/_release/`, so installs need nothing but the wheel.

## What installers do

1. Pick the newest Python 3.12+: `where python` and `py -0p` on Windows, `PATH`
   elsewhere, or `MEOW_PYTHON`. Virtual environments resolve to their base.
2. Create a staging venv and run `pip install --no-deps --require-hashes
   --only-binary :all:` for exactly the manifest's version and wheel hash.
3. Run the new release's `python -m meow.installer.releases --finish`: install
   `requirements-release.txt` with `--require-hashes`, build the launcher
   (Windows uses the .NET Framework compiler included with Windows), copy the
   plugin, and swap the staging folder into `~/.meow/versions/TAG`. An existing
   folder is set aside until the new one is in place, so reruns repair installs
   built by another or removed Python.
4. Register the plugin and switch the launchers and `current-version`.

`meow -u` runs the same steps with the newest Python it finds. Pip settings such
as `PIP_INDEX_URL` apply, so mirrors work. Run `python scripts/smoke_release.py`
to exercise the installer against a locally built wheel.

The versioned runtimes intentionally remain after updates so active workers
and OS schedules can still launch their original interpreter. Keep those
versions until their jobs have finished or their schedules are recreated. A
venv depends on the Python that built it; if that Python is removed, rerun the
installer.
