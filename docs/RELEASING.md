# Release Guide

These are my notes on handling releases.
I should probably write a script to automate this 😅

1. Set the `__version__` variable in tgm.py, e.g. `__version__ = "1.0.0"`.
2. Make sure all modifications are committed and pushed.
3. Tag the latest commit according to the previous version, e.g. `v1.0.0`.
4. Create the GitHub release with notes, and upload `tgm.py` as an asset.

Note that immutable releases are enabled! If tgm.py is not included or is
incorrect, backup the release notes, yank (delete) the release immediately,
and repeat the process with a patch bump to the version.
