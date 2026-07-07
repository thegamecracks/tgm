from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pytest import LogCaptureFixture

from tgm import Config, FixMeta

if TYPE_CHECKING:
    from tests.conftest import Workshop


def test_fix_meta(config: Config, workshop: Workshop) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id, is_updated=False)
    FixMeta(config, dry_run=False).invoke()

    meta = workshop_path.joinpath("meta.cpp").read_text()
    m = re.search(r"publishedid = (\d+);", meta)
    assert m is not None, "missing publishedid in meta.cpp"
    assert m[1] == str(item_id), "failed to set publishedid"


def test_fix_meta_dry_run(config: Config, workshop: Workshop) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id, is_updated=False)
    FixMeta(config, dry_run=True).invoke()

    meta = workshop_path.joinpath("meta.cpp").read_text()
    m = re.search(r"publishedid = (\d+);", meta)
    assert m is not None, "missing publishedid in meta.cpp"
    assert m[1] == "0", "violated dry run"


def test_fix_meta_file_not_found(
    config: Config,
    workshop: Workshop,
    caplog: LogCaptureFixture,
) -> None:
    item_id = 1234567890
    workshop_path = workshop.install_workshop_item(item_id)
    workshop_path.joinpath("meta.cpp").unlink()
    FixMeta(config, dry_run=False).invoke()

    assert caplog.records[0].getMessage() == f"Missing meta.cpp in {item_id}"


def test_fix_meta_add_publishedid(
    config: Config,
    workshop: Workshop,
) -> None:
    item_id = 1234567890
    content = "foo = 123;\n"
    workshop_path = workshop.install_workshop_item(item_id)
    workshop_path.joinpath("meta.cpp").write_text(content)
    FixMeta(config, dry_run=False).invoke()

    meta = workshop_path.joinpath("meta.cpp").read_text()
    assert meta == f"{content}publishedid = {item_id};\n"
