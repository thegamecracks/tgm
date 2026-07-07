import itertools
import argparse
from pathlib import Path
from typing import Sequence

import pytest
from pytest import MonkeyPatch

from tgm import Command, Config, Undefined


class Workshop:
    def __init__(self, *, key_dir: Path, mod_dir: Path, workshop_dir: Path) -> None:
        self.key_dir = key_dir
        self.mod_dir = mod_dir
        self.workshop_dir = workshop_dir

    def install_workshop_item(
        self,
        item_id: int,
        *,
        addons: Sequence[str] = ("TestAddon.pbo",),
        keys: Sequence[str] = ("TestKey.bikey",),
        is_lowercase: bool = True,
        is_updated: bool = True,
    ) -> Path:
        workshop_path = self.workshop_dir / str(item_id)
        workshop_path.mkdir()

        addons_dir = "addons" if is_lowercase else "Addons"
        addons_dir = workshop_path / addons_dir
        addons_dir.mkdir()

        key_cycle = itertools.cycle(keys)

        for filename in addons:
            if is_lowercase:
                filename = filename.lower()

            addons_dir.joinpath(filename).touch()

            if next_key := next(key_cycle, None):
                # NOTE: bisign is not fully lowercased
                bisign = Path(f"{filename}.{next_key}").with_suffix(".bisign")
                addons_dir.joinpath(bisign).touch()

        publishedid = item_id if is_updated else 0
        workshop_path.joinpath("meta.cpp").write_text(f"publishedid = {publishedid};")
        workshop_path.joinpath("mod.cpp").touch()

        return workshop_path


@pytest.fixture
def workshop(tmp_path: Path) -> Workshop:
    key_dir = tmp_path / "keys"
    mod_dir = tmp_path / "mods"
    workshop_dir = tmp_path / "workshop"

    key_dir.mkdir()
    mod_dir.mkdir()
    workshop_dir.mkdir()

    return Workshop(
        key_dir=key_dir,
        mod_dir=mod_dir,
        workshop_dir=workshop_dir,
    )


@pytest.fixture
def config(workshop: Workshop) -> Config:
    return Config(
        args=argparse.Namespace(),
        command_cls=Command,
        ignore_api_errors=False,
        key_dir=workshop.key_dir,
        mod_dir=workshop.mod_dir,
        steamcmd=None,
        steamcmd_user=Undefined("USER"),
        verbose=0,
        workshop_dir=workshop.workshop_dir,
    )


# TODO: add tests for functions that use these libraries
@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delattr("tgm.subprocess")


@pytest.fixture(autouse=True)
def no_urllib(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delattr("tgm.urllib")
