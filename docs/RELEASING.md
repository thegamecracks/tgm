# Release Guide

These are my notes on handling releases.
I should probably write a script to automate this 😅

1. Set the version in pyproject.toml and uv.lock, e.g. `uv version --bump minor`.
2. Set the `__version__` variable in tgm.py, e.g. `__version__ = "1.0.0"`.
3. Make sure all modifications are committed and pushed.
4. Tag the latest commit according to the previous version, e.g. `v1.0.0`.
5. (Windows) Make sure the line endings in `tgm.py` are LF, **not** CRLF.
6. Create the GitHub release with notes, and upload `tgm.py` as an asset.

Note that immutable releases are enabled! If tgm.py is not included or is
incorrect, backup the release notes, yank (delete) the release immediately,
and repeat the process with a patch bump to the version.
