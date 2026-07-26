from __future__ import annotations

import datetime
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, TypeAlias, assert_never

from pytest import CaptureFixture, LogCaptureFixture

from tgm import Config, FileDetails, LinkMods, ModLinkType

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
    resolve_symlink_tree(config, item_id, tree)
    mod_path = config.mod_dir / f"@{item_id}"  # assuming LinkMods(fetch=False)
    assert_file_structure(mod_path, tree)


def resolve_symlink_tree(config: Config, item_id: int, tree: Tree) -> Tree:
    def resolve_symlinks(root: Path, tree: Tree) -> None:
        for name, expected in tree.items():
            path = root / name
            if isinstance(expected, Symlink) and expected.target is None:
                expected.target = path
            elif isinstance(expected, dict):
                resolve_symlinks(path, expected)

    workshop_path = config.workshop_dir / f"{item_id}"
    resolve_symlinks(workshop_path, tree)
    return tree


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


def create_default_symlink_tree() -> Tree:
    return {
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


def create_file_details(item_id: int) -> FileDetails:
    now = datetime.datetime.now().astimezone()
    return FileDetails(
        id=item_id,
        title=f"Item {item_id}",
        description="",
        created_at=now,
        updated_at=now,
        size=0,
        tags=[],
    )


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
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
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
        migrate=False,
        prompt=False,
        prune=False,
        _item_ids=None,
        _details=None,
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
        link_type=ModLinkType.DIRECTORY_SYMLINK,
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
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
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()
    expected = create_default_symlink_tree()
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
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
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
        link_type=ModLinkType.SYMLINK_TREE,
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    expected = create_default_symlink_tree()
    expected["mod.cpp"] = File()
    expected["user-file"] = File()
    assert_symlink_tree_structure(config, item_id, expected)

    assert caplog.messages[-3].startswith("Removing empty directory:")
    assert caplog.messages[-3].endswith("user-dir")
    assert caplog.messages[-2] == "Removing broken link: broken"
    assert caplog.messages[-1].startswith("File conflict, cannot create symlink")
    assert caplog.messages[-1].endswith("mod.cpp")


def test_link_mods_suggest_migration_to_directory_symlinks(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
) -> None:
    item_id = 1234567890
    workshop.install_workshop_item(item_id)

    # Initial symlink tree
    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.SYMLINK_TREE,
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    expected = create_default_symlink_tree()
    assert_symlink_tree_structure(config, item_id, expected)
    assert not caplog.messages

    # Switch to directory symlinks
    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.DIRECTORY_SYMLINK,
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    assert_symlink_tree_structure(config, item_id, expected)
    assert caplog.messages[0] == (
        "1 linked mods are in an un-preferred format for for your platform.\n"
        "Consider replacing them with 'link-mods --migrate directory-symlinks' (potentially destructive!)"
    )


def test_link_mods_suggest_migration_to_symlink_trees(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id)

    # Initial directory symlink
    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.DIRECTORY_SYMLINK,
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    expected: Tree = {f"@{item_id}": Symlink(target=workshop_path)}
    assert_file_structure(config.mod_dir, expected)
    assert not caplog.messages

    # Switch to symlink trees
    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.SYMLINK_TREE,
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    assert_file_structure(config.mod_dir, expected)
    assert caplog.messages[0] == (
        "1 linked mods are in an un-preferred format for for your platform.\n"
        "Consider replacing them with 'link-mods --migrate symlink-trees' (potentially destructive!)"
    )


def test_link_mods_migrate_to_directory_symlinks(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
    capsys: CaptureFixture[str],
) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id)

    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.SYMLINK_TREE,
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    expected = create_default_symlink_tree()
    assert_symlink_tree_structure(config, item_id, expected)

    assert not caplog.messages
    out, err = capsys.readouterr()
    assert (
        out
        == "LINK: @1234567890                                        <= 1234567890\n"
    )
    assert err == ""

    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.DIRECTORY_SYMLINK,
        migrate=True,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    expected: Tree = {f"@{item_id}": Symlink(target=workshop_path)}
    assert_file_structure(config.mod_dir, expected)

    assert not caplog.messages
    out, err = capsys.readouterr()
    assert out == (
        "REMOVE: @1234567890\n"
        "LINK: @1234567890                                        <= 1234567890\n"
    )
    assert err == ""


def test_link_mods_migrate_to_symlink_trees(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
    capsys: CaptureFixture[str],
) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id)

    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.DIRECTORY_SYMLINK,
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    expected: Tree = {f"@{item_id}": Symlink(target=workshop_path)}
    assert_file_structure(config.mod_dir, expected)

    assert not caplog.messages
    out, err = capsys.readouterr()
    assert (
        out
        == "LINK: @1234567890                                        <= 1234567890\n"
    )
    assert err == ""

    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.SYMLINK_TREE,
        migrate=True,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    expected = create_default_symlink_tree()
    assert_symlink_tree_structure(config, item_id, expected)

    assert not caplog.messages
    out, err = capsys.readouterr()
    assert out == (
        "REMOVE: @1234567890\n"
        "LINK: @1234567890                                        <= 1234567890\n"
    )
    assert err == ""


def test_link_mods_migrate_dry_run(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
    capsys: CaptureFixture[str],
) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id)

    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.DIRECTORY_SYMLINK,
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    expected: Tree = {f"@{item_id}": Symlink(target=workshop_path)}
    assert_file_structure(config.mod_dir, expected)

    assert not caplog.messages
    out, err = capsys.readouterr()
    assert (
        out
        == "LINK: @1234567890                                        <= 1234567890\n"
    )
    assert err == ""

    LinkMods(
        config,
        dry_run=True,
        fetch=False,
        link_type=ModLinkType.SYMLINK_TREE,
        migrate=True,
        prompt=False,
        prune=True,
        _item_ids=None,
        _details=None,
    ).invoke()

    assert_file_structure(config.mod_dir, expected)

    assert not caplog.messages
    out, err = capsys.readouterr()
    assert out == (
        "REMOVE: @1234567890\n"
        "LINK: @1234567890_2                                      <= 1234567890\n"
        # FIXME: don't enumerate new symlink when old symlink is meant to be deleted
    )
    assert err == ""


def test_link_mods_migrate_noop(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
    capsys: CaptureFixture[str],
) -> None:
    workshop.install_workshop_item(1)

    for _ in range(2):
        LinkMods(
            config,
            dry_run=False,
            fetch=False,
            link_type=ModLinkType.DIRECTORY_SYMLINK,
            migrate=True,
            prompt=False,
            prune=True,
            _item_ids=None,
            _details=None,
        ).invoke()

    assert not caplog.messages
    out, err = capsys.readouterr()
    assert out == "LINK: @1                                                 <= 1\n"
    assert err == ""


def test_link_mods_migrate_items_only(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
    capsys: CaptureFixture[str],
) -> None:
    class Item(NamedTuple):
        details: FileDetails
        workshop_path: Path

    installed = {
        1: Item(create_file_details(1), workshop.install_workshop_item(1)),
        2: Item(create_file_details(2), workshop.install_workshop_item(2)),
    }

    # User installs workshop mods with tgm.py 1.0.1
    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.DIRECTORY_SYMLINK,
        migrate=False,
        prompt=False,
        prune=True,
        _item_ids=installed,
        _details={item_id: item.details for item_id, item in installed.items()},
    ).invoke()

    expected: Tree = {
        "@item_1": Symlink(target=installed[1].workshop_path),
        "@item_2": Symlink(target=installed[2].workshop_path),
    }
    assert_file_structure(config.mod_dir, expected)

    assert not caplog.messages
    out, err = capsys.readouterr()
    assert out == (
        "LINK: @item_1                                            <= 1\n"
        "LINK: @item_2                                            <= 2\n"
    )
    assert err == ""

    # User upgrades to tgm.py 2.0.0 and updates @item_2
    LinkMods(
        config,
        dry_run=False,
        fetch=False,
        link_type=ModLinkType.SYMLINK_TREE,
        migrate=True,
        prompt=False,
        prune=True,
        _item_ids={2},
        _details={2: installed[2].details},
    ).invoke()

    expected: Tree = {
        "@item_1": Symlink(target=installed[1].workshop_path),
        "@item_2": resolve_symlink_tree(config, 2, create_default_symlink_tree()),
    }
    assert_file_structure(config.mod_dir, expected)

    assert caplog.messages[0] == (
        "1 linked mods are in an un-preferred format for for your platform.\n"
        "Consider replacing them with 'link-mods --migrate symlink-trees' (potentially destructive!)"
    )
    out, err = capsys.readouterr()
    assert out == (
        "REMOVE: @item_2\n"
        "LINK: @item_2                                            <= 2\n"
    )
    assert err == ""
