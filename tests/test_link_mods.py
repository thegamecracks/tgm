from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, assert_never

from pytest import LogCaptureFixture

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


Tree: TypeAlias = "dict[str, File | Tree]"


def assert_symlink_tree_structure(config: Config, item_id: int, tree: Tree) -> None:
    def resolve_symlinks(root: Path, tree: Tree) -> None:
        for name, expected in tree.items():
            path = root / name
            if isinstance(expected, Symlink) and expected.target is None:
                expected.target = path
            elif isinstance(expected, dict):
                resolve_symlinks(path, expected)

    workshop_path = config.workshop_dir / f"{item_id}"
    mod_path = config.mod_dir / f"@{item_id}"  # assuming LinkMods(fetch=False)
    resolve_symlinks(workshop_path, tree)
    assert_file_structure(mod_path, tree)


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
    path: Path,
    expected: dict[str, Any] | File,
    *,
    _true_root: Path,
) -> str | None:
    p = path.relative_to(_true_root)
    if isinstance(expected, dict):
        if not path.is_dir() or path.is_symlink():
            return f"{p} must be a directory"
    elif isinstance(expected, Symlink):
        if not path.is_symlink():
            return f"{p} must be a symlink"
        elif expected.target is None:
            return f"{p} requires a symlink target (test needs to be fixed)"
        elif path.resolve() != expected.target:
            return f"{p} must resolve to {expected.target}"
    elif isinstance(expected, File):
        if not path.is_file() or path.is_symlink():
            return f"{p} must be a file"
    else:
        assert_never(expected)


def test_link_mods_directory_symlink(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id)

    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.DIRECTORY_SYMLINK,
        prompt=False,
        prune=True,
    ).invoke()

    expected: Tree = {f"@{item_id}": Symlink(target=workshop_path)}
    assert_file_structure(config.mod_dir, expected)
    assert not caplog.messages


def test_link_mods_directory_symlink_prune(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
) -> None:
    expected: Tree
    config.mod_dir.joinpath("@first_mod").symlink_to(config.workshop_dir / "1")
    config.mod_dir.joinpath("@second_mod").symlink_to(config.workshop_dir / "2")

    # --no-prune
    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.DIRECTORY_SYMLINK,
        prompt=False,
        prune=False,
    ).invoke()
    expected = {
        "@first_mod": Symlink(target=config.workshop_dir / "1"),
        "@second_mod": Symlink(target=config.workshop_dir / "2"),
    }
    assert_file_structure(config.mod_dir, expected)
    assert not caplog.messages

    # --prune
    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=None,
        prompt=False,
        prune=True,
    ).invoke()
    expected = {}
    assert_file_structure(config.mod_dir, expected)
    assert "Removing broken link: @first_mod" in caplog.messages
    assert "Removing broken link: @second_mod" in caplog.messages


def test_link_mods_symlink_tree(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
) -> None:
    item_id = 1234567890
    workshop.install_workshop_item(item_id)
    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.SYMLINK_TREE,
        prompt=False,
        prune=True,
    ).invoke()
    expected: Tree = {
        "addons": {
            "testaddon.pbo": Symlink(),
            "testaddon.pbo.TestKey.bisign": Symlink(),
        },
        "keys": {
            "TestKey.bikey": Symlink(),
        },
        ".tgm_symlink.json": File(),
        "meta.cpp": Symlink(),
        "mod.cpp": Symlink(),
    }
    assert_symlink_tree_structure(config, item_id, expected)
    assert not caplog.messages


def test_link_mods_symlink_tree_repair(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
) -> None:
    item_id = 1234567890
    workshop.install_workshop_item(item_id)
    mod_path = config.mod_dir / f"@{item_id}"

    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.SYMLINK_TREE,
        prompt=False,
        prune=True,
    ).invoke()

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
    # Add a conflicting file, must be kept
    mod_path.joinpath("mod.cpp").unlink()
    mod_path.joinpath("mod.cpp").touch()

    # Re-running command should repair the symlink tree
    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=None,
        prompt=False,
        prune=True,
    ).invoke()

    expected: Tree = {
        "addons": {
            "testaddon.pbo": Symlink(),
            "testaddon.pbo.TestKey.bisign": Symlink(),
        },
        "keys": {
            "TestKey.bikey": Symlink(),
        },
        ".tgm_symlink.json": File(),
        "meta.cpp": Symlink(),
        "mod.cpp": File(),
        "user-file": File(),
    }
    assert_symlink_tree_structure(config, item_id, expected)

    assert caplog.messages[0].startswith("Removing empty directory:")
    assert caplog.messages[0].endswith("user-dir")
    assert caplog.messages[1] == "Removing broken link: broken"
    assert caplog.messages[2].startswith("File conflict, cannot create symlink")
    assert caplog.messages[2].endswith("mod.cpp")
