import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest import CaptureFixture, LogCaptureFixture, MonkeyPatch

import tgm
from tgm import CommandError, Config, HTTPError, SelfUpdate, Version


def test_version_parsing() -> None:
    assert Version.from_str("0.1.0") == Version(major=0, minor=1, patch=0)
    assert Version.from_str("1.0.0") == Version(major=1, minor=0, patch=0)
    assert Version.from_str("1.0.0a1") == Version(
        major=1, minor=0, patch=0, suffix="a1"
    )
    assert Version.from_str("1!2026.7.12rc1") == Version(
        epoch=1, major=2026, minor=7, patch=12, suffix="rc1"
    )


def test_version_str() -> None:
    assert str(Version.from_str("1.2.3.4a5.dev6+ham")) == "1.2.3.4a5.dev6+ham"
    assert str(Version(major=1, minor=0, patch=0)) == "1.0.0"
    assert str(Version(major=1, minor=0, patch=0, suffix="a1")) == "1.0.0a1"
    assert str(Version(epoch=1, major=1, minor=0, patch=0, suffix="a1")) == "1!1.0.0a1"


def test_version_is_pre_release() -> None:
    assert not Version.from_str("1.0.0").is_pre_release()
    assert Version.from_str("1.0.0a1").is_pre_release()
    assert Version.from_str("1.0.0.a1").is_pre_release()
    assert Version.from_str("1.0.0b1").is_pre_release()
    assert Version.from_str("1.0.0rc1").is_pre_release()


@pytest.mark.parametrize(
    "old,new",
    [
        ("0.1.0", "1.0.0"),
        ("1.0.0", "1.0.1"),
        ("1.0.1", "1.1.0"),
        ("1.1.0", "2.0.0"),
        ("2.0.0", "1!1.0.0"),
        ("1.0.0a1", "1.0.0"),
        ("1.0.0a1", "1.0.0b1"),
        ("1.0.0", "1.0.0.post1"),
        ("1.0.0.post1", "1.0.0.post2"),
        # ("1.0.0.post9", "1.0.0.post10"),
    ],
)
def test_version_ordering(old: str, new: str) -> None:
    old_version = Version.from_str(old)
    new_version = Version.from_str(new)
    assert old_version != new_version
    assert old_version < new_version
    assert new_version > old_version


class FakeRelease:
    def __init__(self, monkeypatch: MonkeyPatch) -> None:
        self.response_mock = MagicMock(spec_set=["status", "read"])
        self.save_mock = MagicMock(spec_set=[])

        self._latest_content = ""
        self._monkeypatch = monkeypatch

        self.set_response_status(200)
        monkeypatch.setattr(tgm, "http_get", self._http_get)
        monkeypatch.setattr(SelfUpdate, "_save_to_file", self.save_mock)

    @contextmanager
    def _http_get(self, url: str) -> Iterator[MagicMock]:
        assert url == SelfUpdate._download_url
        yield self.response_mock

    def set_current_version(self, version: str) -> None:
        self._monkeypatch.setattr(tgm, "__version__", version)

    def set_latest_version(self, version: str) -> None:
        self._latest_content = f'__version__ = "{version}"'

    def set_response_status(self, status: int) -> None:
        self.response_mock.status = status
        if status == 200:
            self.response_mock.read.side_effect = lambda: self._latest_content.encode()
        else:
            self.response_mock.read.side_effect = RuntimeError(
                f"read forbidden with mock status {status}"
            )


@pytest.fixture(autouse=True)
def release(monkeypatch: MonkeyPatch) -> FakeRelease:
    release = FakeRelease(monkeypatch)
    return release


def test_self_update(
    config: Config,
    release: FakeRelease,
    capsys: CaptureFixture[str],
) -> None:
    release.set_current_version("1.0.0")
    release.set_latest_version("2.0.0")

    SelfUpdate(config, dry_run=False).invoke()

    release.save_mock.assert_called_once_with(
        Path(tgm.__file__),
        release._latest_content,
    )

    out, err = capsys.readouterr()
    assert out == "UPDATE: tgm.py (1.0.0 -> 2.0.0)\n"
    assert err == ""


def test_self_update_dry_run(
    config: Config,
    release: FakeRelease,
    capsys: CaptureFixture[str],
) -> None:
    release.set_current_version("1.0.0")
    release.set_latest_version("2.0.0")

    SelfUpdate(config, dry_run=True).invoke()

    release.save_mock.assert_not_called()

    out, err = capsys.readouterr()
    assert out == "UPDATE: tgm.py (1.0.0 -> 2.0.0)\n"
    assert err == ""


def test_self_update_final_to_pre_release(
    config: Config,
    release: FakeRelease,
    caplog: LogCaptureFixture,
    capsys: CaptureFixture[str],
) -> None:
    release.set_current_version("1.0.0")
    release.set_latest_version("2.0.0a1")
    caplog.set_level(logging.INFO)

    SelfUpdate(config, dry_run=False).invoke()

    assert (
        caplog.messages[-1] == "No update available (current: 1.0.0, latest: 2.0.0a1)"
    )
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""


def test_self_update_pre_release_to_final(
    config: Config,
    release: FakeRelease,
    capsys: CaptureFixture[str],
) -> None:
    release.set_current_version("1.0.0a1")
    release.set_latest_version("1.0.0")

    SelfUpdate(config, dry_run=False).invoke()

    out, err = capsys.readouterr()
    assert out == "UPDATE: tgm.py (1.0.0a1 -> 1.0.0)\n"
    assert err == ""


def test_self_update_not_a_file(config: Config, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delattr(tgm, "__file__")
    with pytest.raises(CommandError, match="Cannot self-update unless saved to a file"):
        SelfUpdate(config, dry_run=False).invoke()


def test_self_update_not_found(config: Config, release: FakeRelease) -> None:
    release.set_response_status(404)
    with pytest.raises(CommandError, match="Could not download the latest release"):
        SelfUpdate(config, dry_run=False).invoke()


def test_self_update_bad_status_code(config: Config, release: FakeRelease) -> None:
    release.set_response_status(500)
    with pytest.raises(HTTPError):
        SelfUpdate(config, dry_run=False).invoke()


def test_self_update_missing_version(config: Config) -> None:
    with pytest.raises(CommandError, match="Failed to extract version"):
        SelfUpdate(config, dry_run=False).invoke()


def test_self_update_failed_to_save(config: Config, release: FakeRelease) -> None:
    release.set_current_version("1.0.0")
    release.set_latest_version("2.0.0")
    release.save_mock.side_effect = OSError
    with pytest.raises(CommandError, match="Failed to save"):
        SelfUpdate(config, dry_run=False).invoke()
