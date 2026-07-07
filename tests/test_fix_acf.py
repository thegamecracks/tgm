from __future__ import annotations

import importlib.resources
from contextlib import contextmanager
from io import StringIO
from typing import IO, TYPE_CHECKING, Iterator

import pytest
from pytest import CaptureFixture, LogCaptureFixture, MonkeyPatch

from tgm import ACF, Config, FixACF, acf_dump, acf_load

if TYPE_CHECKING:
    from tests.conftest import Workshop

ACF_FILES = importlib.resources.files("tests.acf")


class FakeACF:
    def __init__(self) -> None:
        self.file = StringIO()
        self._deleted = False

    def __repr__(self) -> str:
        return "<FakeACF>"

    @property
    def name(self) -> str:
        return "FakeACF"

    # Technically this shouldn't be a context manager,
    # but we need to block file closing to inspect its contents
    @contextmanager
    def open(self, mode: str = "r", *, encoding: str = "") -> Iterator[IO[str]]:
        if self._deleted:
            raise FileNotFoundError
        elif mode == "r":
            self.file.seek(0)
            yield self.file
        elif mode == "w":
            self.file.seek(0)
            self.file.truncate()
            yield self.file
        else:
            raise RuntimeError(f"Unsupported mode: {mode}")

    def unlink(self) -> None:
        self._deleted = True


@pytest.fixture
def acf(monkeypatch: MonkeyPatch) -> FakeACF:
    path = FakeACF()
    monkeypatch.setattr(FixACF, "_get_acf_path", lambda self: path)
    return path


def test_acf_load() -> None:
    with ACF_FILES.joinpath("simple.acf").open() as f:
        data = acf_load(f)

    assert data == {
        "AppWorkshop": {
            "appid": 107410,
            "SizeOnDisk": 129227,
            "NeedsUpdate": 0,
            "NeedsDownload": 0,
            "TimeLastUpdated": 0,
            "TimeLastAppRan": 0,
            "LastBuildID": 0,
            "WorkshopItemsInstalled": {
                "3132949782": {
                    "size": 129227,
                    "timeupdated": 1775859837,
                    "manifest": 7249107775186064541,
                },
            },
            "WorkshopItemDetails": {
                "3132949782": {
                    "manifest": 7249107775186064541,
                    "timeupdated": 1775859837,
                    "timetouched": 1781041153,
                    "latest_timeupdated": 1775859837,
                    "latest_manifest": 7249107775186064541,
                },
            },
        },
    }


def test_acf_dump() -> None:
    data = ACF(
        {
            "AppWorkshop": {
                "appid": 107410,
                "SizeOnDisk": 129227,
                "NeedsUpdate": 0,
                "NeedsDownload": 0,
                "TimeLastUpdated": 0,
                "TimeLastAppRan": 0,
                "LastBuildID": 0,
                "WorkshopItemsInstalled": {
                    "3132949782": {
                        "size": 129227,
                        "timeupdated": 1775859837,
                        "manifest": 7249107775186064541,
                    },
                },
                "WorkshopItemDetails": {
                    "3132949782": {
                        "manifest": 7249107775186064541,
                        "timeupdated": 1775859837,
                        "timetouched": 1781041153,
                        "latest_timeupdated": 1775859837,
                        "latest_manifest": 7249107775186064541,
                    },
                },
            },
        }
    )

    generated = StringIO()
    acf_dump(generated, data)

    expected = ACF_FILES.joinpath("simple.acf").read_text()
    assert generated.getvalue() == expected


def test_acf_load_empty() -> None:
    with StringIO() as f:
        assert acf_load(f) == {}


def test_acf_load_key_without_value() -> None:
    with StringIO("abc123") as f, pytest.raises(ValueError):
        acf_load(f)


def test_fix_acf_file_not_found(
    config: Config,
    acf: FakeACF,
    caplog: LogCaptureFixture,
) -> None:
    """Test that FixACF warns if the cache is missing."""
    acf.unlink()
    FixACF(config, dry_run=True, _item_ids=()).invoke()
    assert caplog.records[0].getMessage() == "ACF metadata not found: <FakeACF>"


def test_fix_acf_malformed(config: Config, acf: FakeACF) -> None:
    """Test that FixACF fails with a malformed cache."""
    with acf.open("w") as f:
        f.write("abc123")

    with pytest.raises(ValueError):
        FixACF(config, dry_run=True, _item_ids=()).invoke()


def test_fix_acf(config: Config, acf: FakeACF, capsys: CaptureFixture) -> None:
    """Test that FixACF removes uninstalled mods from the ACF cache."""
    content = ACF_FILES.joinpath("simple.acf").read_text()
    with acf.open("w") as f:
        f.write(content)

    FixACF(config, dry_run=False, _item_ids=()).invoke()

    with acf.open() as f:
        assert acf_load(f) == {
            "AppWorkshop": {
                "appid": 107410,
                "SizeOnDisk": 0,
                "NeedsUpdate": 0,
                "NeedsDownload": 0,
                "TimeLastUpdated": 0,
                "TimeLastAppRan": 0,
                "LastBuildID": 0,
                "WorkshopItemsInstalled": {},
                "WorkshopItemDetails": {},
            }
        }

    out, err = capsys.readouterr()
    assert out == "MODIFY: <FakeACF>\n"
    assert err == ""


def test_fix_acf_dry_run(config: Config, acf: FakeACF, capsys: CaptureFixture) -> None:
    """Test that FixACF adheres to the dry run mode."""
    content = ACF_FILES.joinpath("simple.acf").read_text()
    with acf.open("w") as f:
        f.write(content)

    FixACF(config, dry_run=True, _item_ids=()).invoke()

    expected = acf_load(StringIO(content))
    with acf.open() as f:
        assert acf_load(f) == expected

    out, err = capsys.readouterr()
    assert out == "MODIFY: <FakeACF>\n"
    assert err == ""


def test_fix_acf_installed(
    config: Config,
    acf: FakeACF,
    workshop: Workshop,
    capsys: CaptureFixture,
) -> None:
    """Test that FixACF does nothing when the cache matches the workshop directory."""
    content = ACF_FILES.joinpath("simple.acf").read_text()
    with acf.open("w") as f:
        f.write(content)

    workshop.install_workshop_item(3132949782)
    FixACF(config, dry_run=False, _item_ids=()).invoke()

    expected = acf_load(StringIO(content))
    with acf.open() as f:
        assert acf_load(f) == expected

    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""


def test_fix_acf_invoked_to_remove_item(
    config: Config,
    acf: FakeACF,
    workshop: Workshop,
    capsys: CaptureFixture,
) -> None:
    """Test that FixACF can remove items from cache even when the item is installed."""
    content = ACF_FILES.joinpath("simple.acf").read_text()
    with acf.open("w") as f:
        f.write(content)

    workshop.install_workshop_item(3132949782)
    FixACF(config, dry_run=False, _item_ids=(3132949782,)).invoke()

    with acf.open() as f:
        assert acf_load(f) == {
            "AppWorkshop": {
                "appid": 107410,
                "SizeOnDisk": 0,
                "NeedsUpdate": 0,
                "NeedsDownload": 0,
                "TimeLastUpdated": 0,
                "TimeLastAppRan": 0,
                "LastBuildID": 0,
                "WorkshopItemsInstalled": {},
                "WorkshopItemDetails": {},
            }
        }

    out, err = capsys.readouterr()
    assert out == "MODIFY: <FakeACF>\n"
    assert err == ""
