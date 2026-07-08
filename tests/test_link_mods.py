from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, assert_never, cast

import pytest
from pytest import MonkeyPatch

from tgm import Config, LinkMods, ModLinkType

if TYPE_CHECKING:
    from tests.conftest import Workshop


@dataclass
class File:
    pass


@dataclass
class Symlink(File):
    # FIXME: find a cleaner way to describe the expected target
    target: Path | None = None


Tree: TypeAlias = dict[str, File | dict[str, Any]]


# FIXME: can tree be expressed more concisely? perhaps flattened?
def assert_file_structure(
    root: Path,
    tree: Tree,
    *,
    raise_errors: bool = True,
    _true_root: Path | None = None,
) -> list[str]:
    if _true_root is None:
        _true_root = root

    errors: list[str] = []
    subtrees: dict[Path, dict[str, Any]] = {}

    # Check for all expected files in root
    for name, expected in tree.items():
        path = root / name
        err = _assert_path(path, expected, _true_root=_true_root)
        if err is not None:
            errors.append(err)
            continue

        if isinstance(expected, dict):
            expected = cast(Tree, expected)
            subtrees[path] = expected

    # Check for unexpected files in root
    for path in root.iterdir():
        if path.name not in tree:
            errors.append(f"{path.relative_to(_true_root)} not expected")

    # Recurse into subtrees
    for path, subtree in subtrees.items():
        subtree_errors = assert_file_structure(
            path,
            subtree,
            raise_errors=False,
            _true_root=_true_root,
        )
        errors.extend(subtree_errors)

    if not raise_errors or not errors:
        return errors

    lines = [f"Encountered {len(errors)} validation errors:"]
    for err in errors:
        lines.append(f"    {err}")
    raise AssertionError("\n".join(lines))


def _assert_path(
    path: Path, expected: dict[str, Any] | File, *, _true_root: Path
) -> str | None:
    p = path.relative_to(_true_root)
    if isinstance(expected, dict):
        if not path.is_dir() or path.is_symlink():
            return f"{p} must be a directory"
    elif isinstance(expected, Symlink):
        if not path.is_symlink():
            return f"{p} must be a symlink"
        elif expected.target is not None and path.resolve() != expected.target:
            return f"{p} must resolve to {expected.target}"
    elif isinstance(expected, File):
        if not path.is_file() or path.is_symlink():
            return f"{p} must be a file"
    else:
        assert_never(expected)


@pytest.fixture
def force_directory_symlink(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        LinkMods,
        "_get_link_type",
        lambda self: ModLinkType.DIRECTORY_SYMLINK,
    )


@pytest.fixture
def force_symlink_tree(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        LinkMods,
        "_get_link_type",
        lambda self: ModLinkType.SYMLINK_TREE,
    )


def test_link_mods_directory_symlink(
    config: Config,
    workshop: Workshop,
    force_directory_symlink: None,
) -> None:
    item_id = 1234567890
    workshop.install_workshop_item(item_id)
    LinkMods(config, dry_run=False, fetch=False, prompt=False, prune=True).invoke()
    expected: Tree = {f"@{item_id}": Symlink()}
    assert_file_structure(config.mod_dir, expected)


def test_link_mods_directory_symlink_prune(
    config: Config,
    workshop: Workshop,
    force_directory_symlink: None,
) -> None:
    expected: Tree
    config.mod_dir.joinpath("@first_mod").symlink_to(config.workshop_dir / "1")
    config.mod_dir.joinpath("@second_mod").symlink_to(config.workshop_dir / "2")

    # --no-prune
    LinkMods(config, dry_run=False, fetch=False, prompt=False, prune=False).invoke()
    expected = {
        "@first_mod": Symlink(),
        "@second_mod": Symlink(),
    }
    assert_file_structure(config.mod_dir, expected)

    # --prune
    LinkMods(config, dry_run=False, fetch=False, prompt=False, prune=True).invoke()
    expected = {}
    assert_file_structure(config.mod_dir, expected)


def test_link_mods_symlink_tree(
    config: Config,
    workshop: Workshop,
    force_symlink_tree: None,
) -> None:
    item_id = 1234567890
    workshop.install_workshop_item(item_id)
    LinkMods(config, dry_run=False, fetch=False, prompt=False, prune=True).invoke()
    expected: Tree = {
        f"@{item_id}": {
            "addons": {
                "testaddon.pbo": Symlink(),
                "testaddon.pbo.TestKey.bisign": Symlink(),
            },
            "keys": {
                "TestKey.bikey": Symlink(),
            },
            "meta.cpp": Symlink(),
            "mod.cpp": Symlink(),
            "tgm.metadata": File(),
        },
    }
    assert_file_structure(config.mod_dir, expected)


def test_link_mods_symlink_tree_repair(
    config: Config,
    workshop: Workshop,
    force_symlink_tree: None,
) -> None:
    item_id = 1234567890
    workshop.install_workshop_item(item_id)
    mod_path = config.mod_dir / f"@{item_id}"

    LinkMods(config, dry_run=False, fetch=False, prompt=False, prune=True).invoke()

    # Add a broken symlink, must be removed
    mod_path.joinpath("broken").symlink_to("broken-target")
    # Remove some symlinks, must be restored
    mod_path.joinpath("addons/testaddon.pbo").unlink()
    mod_path.joinpath("meta.cpp").unlink()
    # Remove a directory, must be restored
    shutil.rmtree(mod_path / "keys")
    # Add an unrelated file, must be kept
    mod_path.joinpath("user-file").touch()
    # Add an unrelated directory, must be removed
    mod_path.joinpath("user-dir").mkdir()

    # Re-running command should repair the symlink tree
    LinkMods(config, dry_run=False, fetch=False, prompt=False, prune=True).invoke()

    expected: Tree = {
        f"@{item_id}": {
            "addons": {
                "testaddon.pbo": Symlink(),
                "testaddon.pbo.TestKey.bisign": Symlink(),
            },
            "keys": {
                "TestKey.bikey": Symlink(),
            },
            "meta.cpp": Symlink(),
            "mod.cpp": Symlink(),
            "tgm.metadata": File(),
            "user-file": File(),
        },
    }
    LinkMods(config, dry_run=False, fetch=False, prompt=False, prune=True).invoke()
    assert_file_structure(config.mod_dir, expected)
