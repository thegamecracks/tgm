from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pytest import CaptureFixture, LogCaptureFixture

from tgm import Config, FixMeta

if TYPE_CHECKING:
    from tests.conftest import Workshop


def test_fix_meta(
    config: Config,
    workshop: Workshop,
    capsys: CaptureFixture[str],
) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id, is_updated=False)
    FixMeta(config, dry_run=False, _item_ids=()).invoke()

    meta = workshop_path.joinpath("meta.cpp").read_text()
    m = re.search(r"publishedid = (\d+);", meta)
    assert m is not None, "missing publishedid in meta.cpp"
    assert m[1] == str(item_id), "failed to set publishedid"

    out, err = capsys.readouterr()
    assert out == "FIX: publishedid = 0 <= 1234567890\n"
    assert err == ""


def test_fix_meta_dry_run(
    config: Config,
    workshop: Workshop,
    capsys: CaptureFixture[str],
) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id, is_updated=False)
    FixMeta(config, dry_run=True, _item_ids=()).invoke()

    meta = workshop_path.joinpath("meta.cpp").read_text()
    m = re.search(r"publishedid = (\d+);", meta)
    assert m is not None, "missing publishedid in meta.cpp"
    assert m[1] == "0", "violated dry run"

    out, err = capsys.readouterr()
    assert out == "FIX: publishedid = 0 <= 1234567890\n"
    assert err == ""


def test_fix_meta_file_not_found(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id)
    workshop_path.joinpath("meta.cpp").unlink()
    FixMeta(config, dry_run=False, _item_ids=()).invoke()

    assert caplog.messages[0] == f"Missing meta.cpp in {item_id}"


def test_fix_meta_add_publishedid(
    config: Config,
    workshop: Workshop,
    capsys: CaptureFixture[str],
) -> None:
    item_id = 1234567890
    content = "foo = 123;\n"
    workshop_path = workshop.install_workshop_item(item_id)
    workshop_path.joinpath("meta.cpp").write_text(content)
    FixMeta(config, dry_run=False, _item_ids=()).invoke()

    meta = workshop_path.joinpath("meta.cpp").read_text()
    assert meta == f"{content}publishedid = {item_id};\n"

    out, err = capsys.readouterr()
    assert out == "ADD: publishedid = 1234567890 <= 1234567890\n"
    assert err == ""


def test_fix_meta_item_ids(
    config: Config,
    workshop: Workshop,
    capsys: CaptureFixture[str],
) -> None:
    item_ids = (1, 3)  # should tolerate unknown item IDs
    meta_1 = workshop.install_workshop_item(1, is_updated=False).joinpath("meta.cpp")
    meta_2 = workshop.install_workshop_item(2, is_updated=False).joinpath("meta.cpp")
    FixMeta(config, dry_run=False, _item_ids=item_ids).invoke()

    m = re.search(r"publishedid = (\d+);", meta_1.read_text())
    assert m is not None, "missing publishedid in 1/meta.cpp"
    assert m[1] == "1", "failed to set publishedid for 1/meta.cpp"

    m = re.search(r"publishedid = (\d+);", meta_2.read_text())
    assert m is not None, "missing publishedid in 2/meta.cpp"
    assert m[1] == "0", "did not skip publishedid for 2/meta.cpp"

    out, err = capsys.readouterr()
    assert out == "FIX: publishedid = 0 <= 1         \n"
    assert err == ""
