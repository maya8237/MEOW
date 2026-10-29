# Releasing MEOW

Commit and push the changes to `main`, then create and push a tag: `X.Y.Z` for
a stable release or `X.Y.ZbN` for a beta.

```bash
git tag 0.1.6      # stable
git tag 0.2.0b1    # beta
git push origin 0.1.6
```

That is the release process. CI stamps the tag's version into the Python package
and both plugin manifests, runs tests and lint, installs the checkout through the
real installers on each platform, then publishes. No prefix, alias tag, or manual
version edits are needed. Commits to `main` alone do not update users.

The tag also replaces `"unreleased"` in `src/meow/project/announcements.py`, the
onboarding options `meow --update` lists; an entry tagged after the release
fails it. After publishing, the workflow commits the new versions and only the
announcements that tag shipped to `main`. If `main` is protected, let GitHub
Actions bypass that rule.

MEOW is published to PyPI as `meow-harness` (the import package and `meow`
command are unchanged). The Release workflow's publish job builds the sdist and
wheel, uploads them with PyPI trusted publishing (no stored token), reads the
wheel's SHA-256 back from PyPI, creates the GitHub Release (notes only, no
attached files). PyPI is the only update source: there is no release-channel
branch. Installers and `meow --update` read the PyPI Simple index and verify the
wheel's SHA-256 from it; stable by default; `--latest-pypi`, `--beta` and `--version` select others. A beta
does not stamp announcements or touch `main`. Publication is serialized and can
be rerun after a later failure.

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
   --only-binary :all:` for exactly the PyPI version and wheel hash. When
   `uv` is installed the venv and install use `uv venv --seed` and `uv pip
   install`; any `uv` failure falls back to `venv` and pip.
3. Run the new release's `python -m meow.installer.releases --finish`: install
   `requirements-release.txt` with `--require-hashes`, build the launcher
   (Windows uses the .NET Framework compiler included with Windows), copy the
   plugin, and swap the staging folder into `~/.meow/versions/TAG`. An existing
   folder is set aside until the new one is in place, so reruns repair installs
   built by another or removed Python.
4. Register the plugin and switch the launchers and `current-version`.

`meow -u` runs the same steps with the newest Python it finds. Index settings
follow the tool that runs: `UV_INDEX_URL` for uv, `PIP_INDEX_URL` for pip, so
mirrors work either way. Run `python scripts/smoke_release.py` to exercise the
installer against a locally built wheel; `--pip-only` hides `uv` to test pip.

The versioned runtimes intentionally remain after updates so active workers
and OS schedules can still launch their original interpreter. Keep those
versions until their jobs have finished or their schedules are recreated. A
venv depends on the Python that built it; if that Python is removed, rerun the
installer.
