from __future__ import annotations

import re
from typing import TYPE_CHECKING

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
