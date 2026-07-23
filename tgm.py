#!/usr/bin/python3
"""Manage workshop mods for an Arma 3 server.

For more information:
    https://github.com/thegamecracks/tgm

"""

# /// script
# dependencies = []
# requires-python = ">=3.11"
# ///
#
# MIT License
#
# Copyright (c) 2025 thegamecracks
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import annotations

import argparse
import datetime
import functools
import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Collection, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from http.client import HTTPResponse
from pathlib import Path
from string import Template
from typing import (
    IO,
    Any,
    ClassVar,
    Literal,
    NewType,
    NoReturn,
    Self,
    TypeAlias,
    overload,
)

__version__ = "1.0.1"


@dataclass
class Undefined:
    placeholder: str

    def __str__(self) -> str:
        return self.placeholder


IS_WINDOWS = platform.system() == "Windows"
KEY_DIR = os.getenv("TGM_KEY_DIR", "serverfiles/keys")
MOD_DIR = os.getenv("TGM_MOD_DIR", "serverfiles/mods")
STEAM_USER = os.getenv("TGM_STEAM_USER", Undefined("USER"))
STEAMCMD = os.getenv("TGM_STEAMCMD")
STEAMCMD_RETRIES = max(int(os.getenv("TGM_STEAMCMD_RETRIES", "5")), 1)  # envvar only
APP_ID = 107410
WORKSHOP_DIR = os.getenv(
    "TGM_WORKSHOP_DIR",
    (
        f"C:/Program Files (x86)/Steam/steamapps/workshop/content/{APP_ID}"
        if IS_WINDOWS
        # FIXME: running LinuxGSM once permanently changes Steam's directory...?
        else f"~/.local/share/Steam/steamapps/workshop/content/{APP_ID}"
        if Path("~/.local/share/Steam").expanduser().is_dir()
        else f"~/Steam/steamapps/workshop/content/{APP_ID}"
    ),
)

# https://steamapi.xpaw.me/#ISteamRemoteStorage/GetCollectionDetails
STEAMAPI_COLLECTION_URL = (
    "https://api.steampowered.com/ISteamRemoteStorage/GetCollectionDetails/v1/"
)
# https://steamapi.xpaw.me/#ISteamRemoteStorage/GetPublishedFileDetails
STEAMAPI_FILEDETAILS_URL = (
    "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
)
WORKSHOP_URL_PATTERN = re.compile(
    r"https://steamcommunity.com/(?:sharedfiles|workshop)/filedetails/\?id=(\d+)"
)
WORKSHOP_URL_TEMPLATE = "https://steamcommunity.com/workshop/filedetails/?id={}"

SubParser: TypeAlias = "argparse._SubParsersAction[argparse.ArgumentParser]"

log = logging.getLogger(__name__)


def main() -> None:
    try:
        config = Config.parse_args()
        config.configure_logging()

        try:
            config.invoke()
        except CommandError as e:
            if config.verbose:
                log.exception(e)
            else:
                log.error("%s\n(To show full traceback, use -v/--verbose)", e)
            sys.exit(1)
    except EOFError:
        print()
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except Exception as e:
        log.exception(e)
        sys.exit(1)


@dataclass(kw_only=True)
class Config:
    args: argparse.Namespace
    command_cls: type[Command]
    ignore_api_errors: bool
    key_dir: Path
    mod_dir: Path
    steamcmd: Path | None
    steamcmd_user: str | Undefined
    verbose: int
    workshop_dir: Path

    @classmethod
    def parse_args(cls) -> Self:
        parser = argparse.ArgumentParser(
            description=clean_doc(__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.suggest_on_error = True  # type: ignore  # available in 3.14
        parser.add_argument(
            "--api-strict",
            action="store_false",
            dest="ignore_api_errors",
            help="Fail if any mods cannot be resolved on the Steam Web API",
        )
        parser.add_argument(
            "--key-dir",
            default=KEY_DIR,
            metavar="TGM_KEY_DIR",
            help="Path to directory where keys are linked (default: %(default)s)",
            type=lambda s: Path(s).expanduser().absolute(),
        )
        parser.add_argument(
            "--mod-dir",
            default=MOD_DIR,
            metavar="TGM_MOD_DIR",
            help="Path to directory where mods are linked (default: %(default)s)",
            type=lambda s: Path(s).expanduser().absolute(),
        )
        parser.add_argument(
            "--steamcmd",
            default=STEAMCMD,
            metavar="TGM_STEAMCMD",
            help="Full path to steamcmd script (default: %(default)s)",
            type=lambda s: Path(s).expanduser().absolute(),
        )
        parser.add_argument(
            "--steamcmd-user",
            default=STEAM_USER,
            metavar="TGM_STEAM_USER",
            help="Username used for SteamCMD commands (default: %(default)s)",
        )
        parser.add_argument(
            "-v",
            "--verbose",
            action="count",
            default=0,
            help="Increase logging verbosity",
        )
        parser.add_argument(
            "--workshop-dir",
            default=WORKSHOP_DIR,
            metavar="TGM_WORKSHOP_DIR",
            help="Path to directory where mods are installed (default: %(default)s)",
            type=lambda s: Path(s).expanduser().absolute(),
        )
        parser.set_defaults(command=None)

        subparsers = parser.add_subparsers()
        commands = sorted(Command.ALL_COMMANDS, key=lambda t: t.__name__.lower())
        for command_cls in commands:
            command_cls.register(subparsers)

        args = parser.parse_args()
        if args.command is None:
            parser.print_help()
            sys.exit(1)

        return cls.from_args(args)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Self:
        return cls(
            args=args,
            command_cls=args.command,
            ignore_api_errors=args.ignore_api_errors,
            key_dir=args.key_dir,
            mod_dir=args.mod_dir,
            steamcmd=args.steamcmd,
            steamcmd_user=args.steamcmd_user,
            verbose=args.verbose,
            workshop_dir=args.workshop_dir,
        )

    def configure_logging(self) -> None:
        if self.verbose > 0:
            level = logging.DEBUG
        else:
            level = logging.INFO

        logging.basicConfig(format="%(levelname)s: %(message)s", level=level)

    def invoke(self) -> None:
        command = self.command_cls.from_config(self)
        command.invoke()


@dataclass
class Command(ABC):
    config: Config

    ALL_COMMANDS: ClassVar[list[type[Command]]] = []

    def __init_subclass__(cls) -> None:
        Command.ALL_COMMANDS.append(cls)

    @classmethod
    @abstractmethod
    def register(cls, subparsers: SubParser) -> None: ...

    @classmethod
    @abstractmethod
    def from_config(cls, config: Config, /) -> Self: ...

    @abstractmethod
    def invoke(self) -> None: ...

    def expand_collection_file_details(
        self,
        items: Iterable[FileDetails],
    ) -> list[FileDetails]:
        return expand_collection_file_details(
            items,
            ignore_errors=self.config.ignore_api_errors,
        )

    def find_mod_links(self) -> dict[Path, Path | None]:
        return find_mod_links(
            mod_dir=self.config.mod_dir,
            workshop_dir=self.config.workshop_dir,
        )

    def get_published_file_details(self, items: Collection[int]) -> list[FileDetails]:
        return get_published_file_details(
            items,
            ignore_errors=self.config.ignore_api_errors,
        )

    def item_modified_at(self, item_id: int) -> datetime.datetime:
        return item_modified_at(item_id, workshop_dir=self.config.workshop_dir)

    def list_installed_items(self) -> dict[int, Path]:
        return list_installed_items(workshop_dir=self.config.workshop_dir)


@dataclass(kw_only=True)
class CheckUpdate(Command):
    """Check workshop mods for updates based on their modification time.

    This command is deprecated and is equivalent to update --all --dry-run --no-fix.

    """

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "check-update",
            aliases=["cu"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Check for workshop mod updates",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        # args = config.args
        return cls(config)

    def invoke(self) -> None:
        Update(
            self.config,
            all=True,
            dry_run=True,
            fix=False,
            force=False,
            item_ids=[],
        ).invoke()


@dataclass(kw_only=True)
class Details(Command):
    """Show details of current workshop mods."""

    fetch: bool

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "details",
            aliases=["dt"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Show workshop mod details",
        )
        parser.add_argument(
            "--fetch",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Fetch workshop items to show extra details",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args
        return cls(config, fetch=args.fetch)

    def invoke(self) -> None:
        installed = self.list_installed_items()
        installed = {item_id: mod.resolve() for item_id, mod in installed.items()}
        if not installed:
            return log.info("No workshop mods installed")

        mod_links = self.find_mod_links()
        details: dict[int, FileDetails] = {}
        if self.fetch:
            details_list = self.get_published_file_details(installed)
            details = {item.id: item for item in details_list}

        rows: list[tuple[int, str, Path, Path | None, FileDetails | None]] = []
        for item_id, mod in installed.items():
            link = mod_links[mod]
            item = details.get(item_id)
            title = (
                item.title
                if item is not None
                else link.name
                if link is not None
                else str(item_id)
            )
            rows.append((item_id, title, mod, link, item))

        rows.sort(key=lambda t: (t[1].lower(), t[0]))
        for item_id, title, mod, link, item in rows:
            self._show_item_details(
                item_id=item_id,
                title=title,
                mod=mod,
                link=link,
                item=details.get(item_id),
            )

    def _show_item_details(
        self,
        *,
        item_id: int,
        title: str,
        mod: Path,
        link: Path | None,
        item: FileDetails | None,
    ) -> None:
        url = WORKSHOP_URL_TEMPLATE.format(item_id)
        modified_at = self.item_modified_at(item_id)

        if item is not None:
            print(title)
            print(f"    Workshop URL:    {url}")
            print(f"    Downloaded:      {modified_at.isoformat(' ')}")
            print(f"    First published: {item.created_at.isoformat(' ')}")
            print(f"    Last updated:    {item.updated_at.isoformat(' ')}")
            print(f"    File size:       {natural_bytes(item.size)}")
            print(f"    Location:        {mod}")
            print(f"    Symlink:         {link}")
        else:
            print(title)
            print(f"    Workshop URL:    {url}")
            print(f"    Downloaded:      {modified_at.isoformat(' ')}")
            print(f"    Location:        {mod}")
            print(f"    Symlink:         {link}")


@dataclass(kw_only=True)
class FixACF(Command):
    """Fix SteamCMD ACF metadata when mods are removed.

    This prevents SteamCMD from re-downloading removed workshop mods.
    See also: https://github.com/RimSort/RimSort/issues/451

    The following path is used to find the metadata, assuming that the workshop
    directory is part of a Steam installation:
        {TGM_WORKSHOP_DIR}/../../appworkshop_107410.acf

    If the file does not exist, this command does nothing.

    If a workshop mod was already partially downloaded by SteamCMD,
    this command will *not* remove its entry from the metadata.
    Only mods with empty or non-existent directories are removed.

    """

    dry_run: bool
    _item_ids: Collection[int]

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "fix-acf",
            aliases=["fa"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Fix SteamCMD ACF metadata when mods are removed",
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Don't overwrite any metadata",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args
        return cls(config, dry_run=args.dry_run, _item_ids=())

    def invoke(self) -> None:
        acf_path = self._get_acf_path()
        try:
            with acf_path.open(encoding="utf-8") as f:
                acf = acf_load(f)
        except FileNotFoundError:
            return log.warning("ACF metadata not found: %s", acf_path)
        except ValueError as e:
            raise ValueError(f"Failed to read {acf_path.name}: {e}") from None

        app_workshop: ACF = acf["AppWorkshop"]
        acf_installed: ACF = app_workshop["WorkshopItemsInstalled"]
        acf_details: ACF = app_workshop["WorkshopItemDetails"]

        all_items: set[str] = set(acf_installed) | set(acf_details)
        installed = list_installed_items(workshop_dir=self.config.workshop_dir)
        invalid_items = all_items - set(str(item_id) for item_id in installed)

        if self._item_ids:
            # Invoked by another command
            invalid_items |= set(str(item_id) for item_id in self._item_ids)

        if not invalid_items:
            return log.info("Nothing to fix in ACF metadata")

        for item_id in invalid_items:
            log.debug("Removing ACF item ID: %s", item_id)
            acf_installed.pop(item_id, None)
            acf_details.pop(item_id, None)
            # TODO: remove manifests from depotcache too?

        app_workshop["SizeOnDisk"] = sum(
            item["size"] for item in app_workshop["WorkshopItemsInstalled"].values()
        )

        print("MODIFY:", acf_path)
        if self.dry_run:
            return

        with acf_path.open("w", encoding="utf-8") as f:
            acf_dump(f, acf)

    def _get_acf_path(self) -> Path:
        # FIXME: this goes outside workshop dir! must be monkeypatched for testing
        return self.config.workshop_dir.parent.parent / f"appworkshop_{APP_ID}.acf"


@dataclass(kw_only=True)
class FixMeta(Command):
    """Validate and fix meta.cpp publishedid fields.

    This ensures that the Arma 3 Launcher can automatically install
    the server's mods when signature verification is enabled.

    """

    dry_run: bool

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "fix-meta",
            aliases=["fm"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Fix meta.cpp files in workshop directory",
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Don't modify any meta.cpp files",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args
        return cls(config, dry_run=args.dry_run)

    def invoke(self) -> None:
        for mod, link in self.find_mod_links().items():
            item_id = int(mod.name)
            name = (
                f"{item_id:<10} ({link.name})" if link is not None else f"{item_id:<10}"
            )

            meta = mod / "meta.cpp"
            if not meta.exists():
                log.warning("Missing meta.cpp in %s", name)
                continue

            content = meta.read_text("utf-8")
            self._fix_publishedid(meta, name, item_id, content)

    def _fix_publishedid(
        self,
        meta: Path,
        name: str,
        item_id: int,
        content: str,
    ) -> None:
        pattern = r"(publishedid\s*=\s*)(\d+)"
        m = re.search(pattern, content)
        if m is None:
            print(f"ADD: publishedid = {item_id:<10} <= {name}")
            content = content + f"publishedid = {item_id};\n"
            if not self.dry_run:
                meta.write_text(content, "utf-8")
            return

        publishedid = int(m[2])
        if publishedid != item_id:
            print(f"FIX: {m[0]} <= {name}")
            content = re.sub(pattern, rf"\g<1>{item_id}", content, count=1)
            if not self.dry_run:
                meta.write_text(content, "utf-8")
            return


@dataclass(kw_only=True)
class Help(Command):
    """Show this program's documentation."""

    section: str

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "help",
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="(Deprecated) Show this program's documentation",
        )
        parser.add_argument(
            "section",
            # choices=sorted(HELP_DOCSTRINGS),
            default="summary",
            help="The section to show help for",
            nargs="?",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args
        return cls(config, section=args.section)

    def invoke(self) -> None:
        print(f"TGM_WORKSHOP_DIR='{self.config.workshop_dir}'")
        print(f"TGM_MOD_DIR='{self.config.mod_dir}'")
        print(f"TGM_KEY_DIR='{self.config.key_dir}'")
        print()
        print("Documentation has been moved to:")
        print("    https://github.com/thegamecracks/tgm")
        print()
        print("For command help, use tgm.py [command] -h/--help.")


@dataclass(kw_only=True)
class Install(Command):
    """Install workshop mods and collections.

    After installation, the lowercase, link-mods, fix-meta, and link-keys commands
    are automatically invoked unless --no-fix is used.

    """

    dry_run: bool
    fix: bool
    item_ids: list[int]
    skip_installed: bool

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "install",
            aliases=["i"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Install workshop mods and collections",
        )
        parser.add_argument(
            "-a",
            "--all",
            action="store_true",
            help="Deprecated alias of --force",
        )
        parser.add_argument(
            "--fix",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Run fixes after install (default: %(default)s)",
        )
        parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="Try installing all mods even if already installed",
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Don't install any mods",
        )
        parser.add_argument(
            "mods",
            help="Workshop mods or collections to install",
            nargs="+",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args

        skip_installed = True
        if args.force:
            skip_installed = False
        elif args.all:
            log.warning("-a/--all is deprecated, use --force instead")
            skip_installed = False

        item_ids = parse_item_ids(args.mods, mod_dir=config.mod_dir)
        return cls(
            config,
            dry_run=args.dry_run,
            fix=args.fix,
            item_ids=item_ids,
            skip_installed=skip_installed,
        )

    def invoke(self) -> None:
        items = self.get_published_file_details(self.item_ids)
        items = self.expand_collection_file_details(items)

        if self.skip_installed:
            installed = self.list_installed_items()
            items = [item for item in items if item.id not in installed]

        if not items:
            return log.info("No workshop mods need to be installed")

        command = SteamCommand.from_config(self.config)
        for item in items:
            print(f"INSTALL: {item.id:<10} ({item.title})")
            command.extend("+workshop_download_item", APP_ID, item.id)

        print(command)
        if not self.dry_run:
            command.call()

        if self.fix:
            LowercaseAddons(self.config, dry_run=self.dry_run).invoke()
            LinkMods(
                self.config,
                dry_run=self.dry_run,
                fetch=False,
                prompt=False,
                prune=False,
            ).invoke_with_items({item.id: item for item in items})
            FixMeta(self.config, dry_run=self.dry_run).invoke()
            LinkKeys(self.config, dry_run=self.dry_run, prune=True).invoke()


@dataclass(kw_only=True)
class LowercaseAddons(Command):
    """Lowercase addons in workshop mods for Arma 3 Linux compatibility."""

    dry_run: bool

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "lowercase",
            aliases=["lw"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Lowercase addons in workshop directory",
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Don't rename any files",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args
        return cls(config, dry_run=args.dry_run)

    def invoke(self) -> None:
        if IS_WINDOWS:
            return log.info("Skipping addon lowercasing on Windows")

        for mod in self.list_installed_items().values():
            addons = self._find_addons_directory(mod)
            if addons is None:
                log.warning("No addons directory found in %s", mod)
                continue

            self._iter_addons(addons)

    def _find_addons_directory(self, mod: Path) -> Path | None:
        workshop_dir = self.config.workshop_dir
        expected = mod / "addons"

        for path in mod.iterdir():
            if not path.is_dir() or path.name.lower() != expected.name:
                continue
            elif path == expected:
                return expected

            print(f"RENAME: {expected.name:50} <= {path.relative_to(workshop_dir)}")
            if not self.dry_run:
                path.rename(expected)
                return expected

            return path

    def _iter_addons(self, addons: Path) -> None:
        types = ("*.pbo", "*.bisign")

        workshop_dir = self.config.workshop_dir
        files = (p for t in types for p in addons.glob(t))
        files = ((old, old.with_name(old.name.lower())) for old in files)
        files = ((old, new) for old, new in files if old != new)

        for old, new in files:
            print(f"RENAME: {new.name:50} <= {old.relative_to(workshop_dir)}")
            if not self.dry_run:
                old.rename(new)


@dataclass(kw_only=True)
class LinkKeys(Command):
    """Link bikey files in the mod directory to the keys directory."""

    dry_run: bool
    prune: bool

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "link-keys",
            aliases=["lk"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Symlink keys from mod directory",
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Don't link any keys",
        )
        parser.add_argument(
            "--prune",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Remove broken links if present (default: %(default)s)",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args
        return cls(config, dry_run=args.dry_run, prune=args.prune)

    def invoke(self) -> None:
        if self.prune:
            remove_broken_links(self.config.key_dir, dry_run=self.dry_run)

        keys = self._find_keys()
        for key in keys:
            self._link_key(key)

    def _find_keys(self) -> list[Path]:
        keys: dict[str, Path] = {}

        for mod in self.find_mod_links().values():
            if mod is None:
                continue

            self._find_keys_in_mod(keys, mod)
            self._check_unsigned_addons(mod)

        return sorted(keys.values())

    def _find_keys_in_mod(self, keys: dict[str, Path], mod: Path) -> None:
        found = False
        for key in mod.rglob("*.bikey"):
            found = True

            if key.name not in keys:
                keys[key.name] = key
            else:
                self._check_conflict(key, keys[key.name])

        if not found:
            log.warning("No keys found in mod: %s", mod.name)

    def _check_conflict(self, old: Path, new: Path) -> None:
        if old.read_bytes() == new.read_bytes():
            return

        with suppress(ValueError):
            old = old.relative_to(self.config.mod_dir)
        with suppress(ValueError):
            new = new.relative_to(self.config.mod_dir)

        raise CommandError(
            f"Key contents differ between {old} and {new}, must be renamed"
        )

    def _link_key(self, key: Path) -> None:
        link = self.config.key_dir / key.name
        if link.is_file() and not link.is_symlink():
            log.warning("Replacing file with link: %s", link.name)
            if not self.dry_run:
                link.unlink()
        elif link.exists():
            self._check_conflict(link, key)
            return

        print(f"LINK: {link.name:50} <= {key.relative_to(self.config.mod_dir)}")
        if not self.dry_run:
            assert key.is_absolute()
            link.symlink_to(key)

    def _check_unsigned_addons(self, mod: Path) -> None:
        addons = mod / "addons"
        if not addons.exists():
            return log.warning(
                "Missing addons directory in mod: %s",
                mod.relative_to(self.config.mod_dir),
            )

        pbos = list(addons.glob("*.pbo"))
        bisigns = list(addons.glob("*.bisign"))
        unsigned = [
            p for p in pbos if not any(s.name.startswith(p.name) for s in bisigns)
        ]
        for pbo in unsigned:
            log.warning("Addon is unsigned: %s", pbo.relative_to(self.config.mod_dir))


@dataclass(kw_only=True)
class LinkMods(Command):
    """Link each mod in the workshop directory to the server mods directory."""

    dry_run: bool
    fetch: bool
    prompt: bool
    prune: bool

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "link-mods",
            aliases=["lm"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Symlink mods from workshop directory",
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Don't create any links",
        )
        parser.add_argument(
            "--fetch",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Fetch workshop items to suggest titles (default: %(default)s)",
        )
        parser.add_argument(
            "--prompt",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Prompt to enter each link's name (default: %(default)s)",
        )
        parser.add_argument(
            "--prune",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Remove broken links if present (default: %(default)s)",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args
        return cls(
            config,
            dry_run=args.dry_run,
            fetch=args.fetch,
            prompt=args.prompt,
            prune=args.prune,
        )

    def invoke(self) -> None:
        if self.prune:
            remove_broken_links(self.config.mod_dir, dry_run=self.dry_run)

        missing = self._find_missing_links()
        if not missing:
            return log.info("No workshop mods need to be linked")

        items = {}
        if self.fetch:
            item_ids = [int(mod.name) for mod in missing]
            items = self.get_published_file_details(item_ids)
            items = {item.id: item for item in items}

        self._create_links(missing, items)

    def invoke_with_items(self, items: Mapping[int, FileDetails | None]) -> None:
        missing = self._find_missing_links()
        missing = [mod for mod in missing if items.get(int(mod.name)) is not None]
        self._create_links(missing, items)

    def _create_links(
        self,
        missing: Collection[Path],
        items: Mapping[int, FileDetails | None],
    ) -> None:
        for mod in missing:
            id = int(mod.name)
            item = items.get(id)
            self._create_link(mod, item=item)

    def _find_missing_links(self) -> list[Path]:
        mod_links = self.find_mod_links()
        missing = [mod for mod, link in mod_links.items() if link is None]
        return missing

    def _create_link(self, mod: Path, item: FileDetails | None) -> None:
        item_id = int(mod.name)
        title = item.title if item is not None else mod.name
        link_name = self._normalize_name(title)

        if self.prompt:
            # TODO: add a way to skip mods?
            print(f"{item_id:<10} ({title})")
            overwrite = input(f"  Link name ({link_name}): ")
            overwrite = self._normalize_name(overwrite)
            link_name = overwrite or link_name

        if not link_name:
            raise CommandError(f"No name available for mod: {mod}")

        link = self._maybe_enumerate_link(self.config.mod_dir / link_name)

        print(f"LINK: {link.name:50} <= {mod.relative_to(self.config.workshop_dir)}")
        if not self.dry_run:
            assert mod.is_absolute()
            link.symlink_to(mod, target_is_directory=True)

    @staticmethod
    def _normalize_name(name: str) -> str:
        name = re.sub(r"\s+", "_", name)
        name = re.sub(r"\W+", "", name)
        name = re.sub(r"_{2,}", "_", name)
        name = name.lower().strip("_ ")
        return f"@{name}" if name else ""

    @staticmethod
    def _maybe_enumerate_link(link: Path) -> Path:
        name = link.name
        n = 1
        while link.exists():
            n += 1
            link = link.with_name(f"{name}_{n}")
        return link


@dataclass(kw_only=True)
class Remove(Command):
    """Remove workshop mods and collections.

    After removal, the fix-acf and link-keys commands are automatically invoked
    unless --no-fix is used. This ensures broken key symlinks are removed
    or symlinked from another mod.

    """

    fetch: bool
    fix: bool
    dry_run: bool
    item_ids: list[int]

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "remove",
            aliases=["rm"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Remove workshop mods and collections",
        )
        parser.add_argument(
            "--fetch",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Fetch workshop items to resolve titles and collections (default: %(default)s)",
        )
        parser.add_argument(
            "--fix",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Run fixes after removal (default: %(default)s)",
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Don't remove any mods",
        )
        parser.add_argument(
            "mods",
            help="Workshop mods or collections to remove",
            nargs="+",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args
        item_ids = parse_item_ids(args.mods, mod_dir=config.mod_dir)
        return cls(
            config,
            dry_run=args.dry_run,
            fetch=args.fetch,
            fix=args.fix,
            item_ids=item_ids,
        )

    def invoke(self) -> None:
        if self.fetch:
            details = self.get_published_file_details(self.item_ids)
            details = self.expand_collection_file_details(details)
            items = {item.id: item for item in details}
        else:
            items = {item: None for item in self.item_ids}

        installed: dict[int, Path] = {}
        for item_id, item in items.items():
            mod = self.config.workshop_dir / str(item_id)
            if mod.is_dir():
                installed[item_id] = mod

        if not installed:
            return log.info("No workshop mods need to be removed")

        for item_id, mod in installed.items():
            item = items.get(item_id)
            if item is not None:
                print(f"REMOVE: {item_id:<10} ({item.title})")
            else:
                print(f"REMOVE: {item_id:<10}")

            if not self.dry_run:
                shutil.rmtree(mod)

        if self.fix:
            FixACF(self.config, dry_run=self.dry_run, _item_ids=installed).invoke()
            LinkKeys(self.config, dry_run=self.dry_run, prune=True).invoke()


@dataclass(kw_only=True)
class SelfUpdate(Command):
    """Update this script by downloading the latest release from GitHub."""

    dry_run: bool

    _download_url: ClassVar[str] = (
        "https://github.com/thegamecracks/tgm/releases/latest/download/tgm.py"
    )

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "self-update",
            aliases=["su"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Update this script from GitHub",
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Check for a new release without updating",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args
        return cls(
            config,
            dry_run=args.dry_run,
        )

    def invoke(self) -> None:
        if "__file__" not in globals() or not (tgm_path := Path(__file__)).exists():
            raise CommandError("Cannot self-update unless saved to a file")

        log.info("Fetching latest release...")
        current_version = Version.from_str(__version__)
        latest_content = self.download_latest_release()
        latest_version = self._extract_version(latest_content)

        if latest_version.is_pre_release() or latest_version <= current_version:
            return log.info(
                "No update available (current: %s, latest: %s)",
                current_version,
                latest_version,
            )

        print(f"UPDATE: {tgm_path.name} ({current_version} -> {latest_version})")
        if self.dry_run:
            return

        try:
            self._save_to_file(tgm_path, latest_content)
        except OSError as e:
            raise CommandError(f"Failed to save to file: {e}") from e

    def download_latest_release(self) -> str:
        with http_get(self._download_url) as response:
            if response.status == 404:
                raise CommandError(
                    "Could not download the latest release of tgm.py!\n"
                    "Please see https://github.com/thegamecracks/tgm/releases "
                    "for more information."
                )
            raise_for_status(response)
            return response.read().decode()

    def _extract_version(self, content: str) -> Version:
        m = re.search(r"""__version__ = ['"]([^'"]+)['"]""", content)
        if m is None:
            raise CommandError(
                "Failed to extract version from the latest release of tgm.py!\n"
                "The release may be corrupted. Please try again later, or see "
                "https://github.com/thegamecracks/tgm/releases for more information."
            )
        return Version.from_str(m[1])

    def _save_to_file(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")


@dataclass(kw_only=True)
class Update(Command):
    """Check and update workshop mods based on their modification time.

    After updating, the lowercase, fix-meta, and link-keys commands
    are automatically invoked unless --no-fix is used.

    """

    all: bool
    dry_run: bool
    fix: bool
    force: bool
    item_ids: list[int]

    @classmethod
    def register(cls, subparsers: SubParser) -> None:
        parser = subparsers.add_parser(
            "update",
            aliases=["u"],
            description=clean_doc(cls.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="Update workshop mods and collections",
        )
        parser.add_argument(
            "-a",
            "--all",
            action="store_true",
            help="Check all installed mods for updates",
        )
        parser.add_argument(
            "--fix",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Run fixes after update (default: %(default)s)",
        )
        parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="Skip modification time checks and always update mods",
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Don't update any mods",
        )
        parser.add_argument(
            "mods",
            help="Workshop mods or collections to update",
            nargs="*",
        )
        parser.set_defaults(command=cls)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        args = config.args
        item_ids = parse_item_ids(args.mods, mod_dir=config.mod_dir)
        return cls(
            config,
            all=args.all,
            dry_run=args.dry_run,
            fix=args.fix,
            force=args.force,
            item_ids=item_ids,
        )

    def invoke(self) -> None:
        installed = self.list_installed_items()
        if not installed:
            return log.info("No workshop mods installed")

        if not self.all and not self.item_ids:
            return log.info(
                "Use -a/--all to update all mods, or specify one or more "
                "workshop mods to update"
            )

        if self.item_ids and not self.all:
            details = self.get_published_file_details(self.item_ids)
            details = self.expand_collection_file_details(details)
            details = [item for item in details if item.id in installed]
        else:
            details = self.get_published_file_details(installed)

        outdated = [item for item in details if self.force or self._is_outdated(item)]
        if not outdated:
            return log.info("No workshop mods need to be updated")

        command = SteamCommand.from_config(self.config)
        for item in outdated:
            date = item.updated_at.date()
            print(f"UPDATE: {date} {item.id:<10} ({item.title})")
            command.extend("+workshop_download_item", APP_ID, str(item.id))

        print(command)
        if not self.dry_run:
            command.call()

        if self.fix:
            LowercaseAddons(self.config, dry_run=self.dry_run).invoke()
            FixMeta(self.config, dry_run=self.dry_run).invoke()
            LinkKeys(self.config, dry_run=self.dry_run, prune=True).invoke()

    def _is_outdated(self, item: FileDetails) -> bool:
        modified = self.item_modified_at(item.id)
        return item.updated_at > modified


@dataclass(kw_only=True)
class CollectionDetails:
    id: int
    children: list[int]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        log.debug("Parsing collection details: %s", data)

        item_id = int(data["publishedfileid"])
        if data["result"] == 9:
            raise DetailsError(f"Item ID not found: {item_id}")
        elif data["result"] != 1:
            r = data["result"]
            raise DetailsError(f"Item ID {item_id} Unexpected result code: {r}")

        return cls(
            id=item_id,
            children=[int(child["publishedfileid"]) for child in data["children"]],
        )


@dataclass(kw_only=True)
class FileDetails:
    id: int
    title: str
    description: str = field(repr=False)
    created_at: datetime.datetime
    updated_at: datetime.datetime
    size: int
    tags: list[Tag]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        log.debug("Parsing file details: %s", data)

        item_id = int(data["publishedfileid"])
        if data["result"] == 9:
            raise DetailsError(f"Item ID not found: {item_id}")
        elif data["result"] != 1:
            r = data["result"]
            raise DetailsError(f"Item ID {item_id} Unexpected result code: {r}")

        created_at = datetime.datetime.fromtimestamp(data["time_created"]).astimezone()
        updated_at = datetime.datetime.fromtimestamp(data["time_updated"]).astimezone()
        return cls(
            id=item_id,
            title=data["title"],
            description=data["description"],
            created_at=created_at,
            updated_at=updated_at,
            size=int(data["file_size"]),
            tags=[Tag.from_dict(tag) for tag in data["tags"]],
        )


class SteamCommand:
    """A SteamCMD command."""

    def __init__(
        self,
        *args: int | str,
        command: Path | None,
        user: str | Undefined | None,
    ) -> None:
        self.command = command
        self.user = user
        self._args: list[str] = [str(x) for x in args]

    def __repr__(self) -> str:
        args = ", ".join(map(repr, self._args))
        return f"{type(self).__name__}({args}, command={self.command!r}, user={self.user!r})"

    def __str__(self) -> str:
        return shlex.join(self.args)

    @property
    def args(self) -> list[str]:
        command = "steamcmd"
        if self.command is not None:
            command = str(self.command.absolute())

        args = [command]
        if self.user is not None:
            args.extend(("+login", str(self.user)))
        args.extend(self._args)
        args.append("+quit")

        return args

    @property
    def cwd(self) -> Path | None:
        if self.command is not None:
            return self.command.absolute().parent

    def call(self) -> None:
        args = self.args

        if self.command is None and shutil.which(args[0]) is None:
            raise SteamNotFoundError()
        if isinstance(self.user, Undefined):
            raise UndefinedSteamUserError()

        retries = STEAMCMD_RETRIES
        for i in range(retries):
            if i > 0:
                log.warning(f"Retrying command {i + 1}/{retries}...")

            with suppress(TimeoutError):
                return self._call(args)

        # https://github.com/ValveSoftware/steam-for-linux/issues/6321#issuecomment-1079914028
        raise CommandError("SteamCMD timed out too many times")

    def extend(self, *args: int | str) -> None:
        self._args.extend(str(x) for x in args)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        return cls(command=config.steamcmd, user=config.steamcmd_user)

    def _call(self, args: list[str]) -> None:
        try:
            subprocess.check_call(args, cwd=self.cwd)
        except subprocess.CalledProcessError as e:
            if e.returncode == 10:
                # Unconfirmed if SteamCMD uses exit code 10 only for timeouts.
                raise TimeoutError("SteamCMD timed out") from None
            else:
                raise CommandError(
                    f"SteamCMD returned non-zero exit status: {e.returncode}"
                ) from None


@dataclass(kw_only=True)
class Tag:
    name: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(name=data["tag"])


@dataclass(kw_only=True)
@functools.total_ordering
class Version:
    # https://packaging.python.org/en/latest/specifications/version-specifiers/#appendix-parsing-version-strings-with-regular-expressions
    # HACK: being zero-dependency means we can't use the packaging library for this...
    epoch: int = 0
    major: int
    minor: int
    patch: int
    suffix: str = ""
    raw: str = field(default="", compare=False)

    def __str__(self) -> str:
        if self.raw:
            return self.raw
        elif self.epoch:
            return f"{self.epoch}!{self.major}.{self.minor}.{self.patch}{self.suffix}"
        return f"{self.major}.{self.minor}.{self.patch}{self.suffix}"

    def __lt__(self, other: Self) -> bool:
        # Approximate PEP 440 ordering, at least with pre-releases
        s = (self.epoch, self.major, self.minor, self.patch)
        o = (other.epoch, other.major, other.minor, other.patch)
        if s < o:
            return True
        elif self.is_pre_release():
            return not other.is_pre_release() or self.suffix < other.suffix
        else:
            return not other.is_pre_release() and self.suffix < other.suffix

    @classmethod
    def from_str(cls, s: str) -> Self:
        m = re.match(
            r"(?:(?P<epoch>\d+)!)?(?P<major>\d+).(?P<minor>\d+).(?P<patch>\d+)(?P<suffix>.*)",
            s,
        )
        if m is None:
            raise ValueError(f"Invalid PEP 440 version specifier: {s}")

        return cls(
            epoch=int(m["epoch"] or 0),
            major=int(m["major"]),
            minor=int(m["minor"]),
            patch=int(m["patch"]),
            suffix=m["suffix"],
            raw=s,
        )

    def is_pre_release(self) -> bool:
        m = re.match(r"[-_\.]?a|b|c|rc|alpha|beta|pre|preview", self.suffix)
        return m is not None


class CommandError(Exception):
    """Raised when an error occurs during command execution."""


class DetailsError(CommandError):
    """Raised when file details cannot be parsed from the Steam Web API."""


class HTTPError(CommandError):
    """Raised when an error occurs with an HTTP request."""


class SteamNotFoundError(CommandError):
    """Raised when attempting to invoke SteamCMD without a valid steamcmd path."""

    def __init__(self) -> None:
        message = clean_doc(
            "SteamCMD could not be found in PATH.\n"
            "Please specify one of --steamcmd, TGM_STEAMCMD=, or --dry-run if applicable.\n"
            "For more information: https://github.com/thegamecracks/tgm#authentication"
        )
        super().__init__(message)


class UndefinedSteamUserError(CommandError):
    """Raised when attempting to invoke SteamCMD without a valid steamcmd path."""

    def __init__(self) -> None:
        message = clean_doc(
            "No username login was given for SteamCMD.\n"
            "Please specify one of --steamcmd-user, TGM_STEAM_USER=, or --dry-run if applicable.\n"
            "For more information: https://github.com/thegamecracks/tgm#authentication"
        )
        super().__init__(message)


ACF = NewType("ACF", dict[str, Any])


def acf_load(file: IO[str], /) -> ACF:
    def raise_syntax(message: str) -> NoReturn:
        s = content[: m.start()]
        lineno = s.count("\n") + 1
        col = len(s.rpartition("\n")[2]) + 1
        raise ValueError(f"{message} at line {lineno} col {col}")

    # Sample .acf file, except real indentation would be tab characters
    # FIXME: support backslash escapes, e.g. "C:\\Program Files (x86)"
    # "AppWorkshop"
    # {
    #     "appid"           "107410"
    #     "SizeOnDisk"      "129227"
    #     "NeedsUpdate"     "0"
    #     "NeedsDownload"   "0"
    #     "TimeLastUpdated" "0"
    #     "TimeLastAppRan"  "0"
    #     "LastBuildID"     "0"
    #     "WorkshopItemsInstalled"
    #     {
    #         "3132949782"
    #         {
    #             "size"        "129227"
    #             "timeupdated" "1775859837"
    #             "manifest"    "7249107775186064541"
    #         }
    #     }
    #     "WorkshopItemDetails"
    #     {
    #         "3132949782"
    #         {
    #             "manifest"           "7249107775186064541"
    #             "timeupdated"        "1775859837"
    #             "timetouched"        "1781041153"
    #             "latest_timeupdated" "1775859837"
    #             "latest_manifest"    "7249107775186064541"
    #         }
    #     }
    # }

    state: Literal["KEY", "VALUE"] = "KEY"
    stack: list[ACF] = [ACF({})]
    operands: list[str] = []
    content = file.read()

    for m in re.finditer(r'"((?:\\.|[^\"\n])*)"|[^\s"]+', content):
        token = m.group(1) or m.group(0)
        block_start = token == "{"
        block_end = token == "}"
        if state == "KEY":
            if block_start:
                raise_syntax("expected KEY, got BLOCK_START")
            elif block_end:
                if len(stack) < 2:
                    raise_syntax("expected KEY or end-of-file, got BLOCK_END")
                else:
                    stack.pop()
            else:
                operands.append(token)
                state = "VALUE"
        elif state == "VALUE":
            key = operands.pop()
            if block_start:
                stack.append(ACF({}))
                stack[-2][key] = stack[-1]
            elif block_end:
                raise_syntax("expected VALUE, got BLOCK_END")
            else:
                try:
                    value = int(token)
                except ValueError:
                    value = token
                stack[-1][key] = value
            state = "KEY"

    if state != "KEY":
        raise_syntax(f"expected {state}, got end-of-file")
    elif len(stack) > 1:
        raise_syntax("expected BLOCK_END, got end-of-file")

    return stack[-1]


def acf_dump(f: IO[str], acf: ACF, /, *, _depth: int = 0) -> None:
    def write(content: str) -> None:
        f.write("\t" * _depth)
        f.write(content)
        f.write("\n")

    def start_block(key: str) -> None:
        write(f'"{key}"')
        write("{")

    def write_entry(key: str, value: str) -> None:
        write(f'"{key}"\t\t"{value}"')

    def end_block() -> None:
        write("}")

    for key, value in acf.items():
        if isinstance(value, dict):
            start_block(key)
            acf_dump(f, value, _depth=_depth + 1)
            end_block()
        else:
            write_entry(key, value)


@overload
def clean_doc(doc: str) -> str: ...


@overload
def clean_doc(doc: None) -> None: ...


def clean_doc(doc: str | None) -> str | None:
    if doc is None:
        return None

    first, _, rest = doc.partition("\n")
    rest = textwrap.dedent(rest)
    doc = f"{first}\n{rest}".strip()
    return Template(doc).safe_substitute(
        prog=Path(sys.argv[0]).name,
    )


def expand_collection_file_details(
    items: Iterable[FileDetails],
    *,
    ignore_errors: bool,
) -> list[FileDetails]:
    collections: list[FileDetails] = []
    ret: list[FileDetails] = []
    for item in items:
        if any(tag.name == "Collection" for tag in item.tags):
            collections.append(item)
        else:
            ret.append(item)

    if collections:
        item_ids = [item.id for item in collections]
        expanded = get_collection_details(item_ids, ignore_errors=ignore_errors)
        expanded = set(id for item in expanded for id in item.children)
        expanded -= {item.id for item in ret}
        expanded = get_published_file_details(expanded, ignore_errors=ignore_errors)
        ret.extend(expanded)

    return ret


def find_mod_links(*, mod_dir: Path, workshop_dir: Path) -> dict[Path, Path | None]:
    mod_links: dict[Path, Path | None] = {}

    for mod in list_installed_items(workshop_dir=workshop_dir).values():
        mod_links[mod.resolve()] = None

    for link in mod_dir.iterdir():
        resolved = link.resolve()
        if resolved not in mod_links:
            continue

        mod_links[resolved] = link

    return mod_links


def get_collection_details(
    item_ids: Collection[int],
    *,
    ignore_errors: bool,
) -> list[CollectionDetails]:
    if not item_ids:
        return []

    data: dict[str, object] = {"collectioncount": len(item_ids)}
    for i, item in enumerate(item_ids):
        data[f"publishedfileids[{i}]"] = str(item)

    with http_post(STEAMAPI_COLLECTION_URL, data=data) as response:
        raise_for_status(response)
        payload = response.read()
        payload = json.loads(payload)

    details = payload["response"]["collectiondetails"]
    items = []
    for item in details:
        try:
            item = CollectionDetails.from_dict(item)
        except DetailsError as e:
            if not ignore_errors:
                raise
            log.warning(e)
        else:
            items.append(item)

    if not ignore_errors and len(items) != len(item_ids):
        raise DetailsError(f"Expected {len(item_ids)} items, but received {len(items)}")

    return items


def get_published_file_details(
    item_ids: Collection[int],
    *,
    ignore_errors: bool,
) -> list[FileDetails]:
    if not item_ids:
        return []

    data: dict[str, object] = {"itemcount": len(item_ids)}
    for i, item in enumerate(item_ids):
        data[f"publishedfileids[{i}]"] = str(item)

    with http_post(STEAMAPI_FILEDETAILS_URL, data=data) as response:
        raise_for_status(response)
        payload = response.read()
        payload = json.loads(payload)

    details = payload["response"]["publishedfiledetails"]
    items = []
    for item in details:
        try:
            item = FileDetails.from_dict(item)
        except DetailsError as e:
            if not ignore_errors:
                raise
            log.warning(e)
        else:
            items.append(item)

    if not ignore_errors and len(items) != len(item_ids):
        raise DetailsError(f"Expected {len(item_ids)} items, but received {len(items)}")

    return items


@contextmanager
def http_get(url: str) -> Iterator[HTTPResponse]:
    headers = {}
    request = urllib.request.Request(url, headers=headers)
    log.debug("%s %s", request.get_method(), request.full_url)
    with urllib.request.urlopen(request, timeout=5) as response:
        assert isinstance(response, HTTPResponse)
        yield response


@contextmanager
def http_post(url: str, data: dict[str, object]) -> Iterator[HTTPResponse]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode(),
        headers={},
        method="POST",
    )

    log.debug("%s %s", request.get_method(), request.full_url)
    with urllib.request.urlopen(request, timeout=5) as response:
        assert isinstance(response, HTTPResponse)
        yield response


def item_modified_at(item_id: int, *, workshop_dir: Path) -> datetime.datetime:
    mod = workshop_dir / str(item_id)
    timestamp = int(mod.stat().st_mtime)
    return datetime.datetime.fromtimestamp(timestamp).astimezone()


def list_installed_items(*, workshop_dir: Path) -> dict[int, Path]:
    # GetPublishedFileDetails doesn't work on legacy UGC, so we need
    # some heuristics to filter them out. Empty directories might have
    # been formerly installed mods that the Steam client didn't clean up.
    return {
        int(mod.name): mod
        for mod in workshop_dir.iterdir()
        if any(mod.iterdir())
        and not (mod / "composition.sqe").exists()
        and not any(mod.glob("*_legacy.bin"))
    }


def natural_bytes(n: int) -> str:
    if n < 1e3:
        return f"{n}B"
    elif n < 1e6:
        return f"{n / 1e3:.3f}KB"
    elif n < 1e9:
        return f"{n / 1e6:.3f}MB"
    return f"{n / 1e9:.3f}GB"


def parse_item_id(s: str, *, mod_dir: Path) -> int:
    try:
        return int(s)
    except ValueError:
        log.debug("Could not parse %r as int", s)

    m = WORKSHOP_URL_PATTERN.match(s)
    if m is not None:
        return int(m[1])
    else:
        log.debug("Could not parse %r as workshop URL", s)

    link = mod_dir / s
    mod = link.resolve()
    try:
        return int(mod.name)
    except ValueError:
        log.debug("Could not parse %r as symlink", s)

    raise ValueError(f"Cannot parse item ID: {s}")


def parse_item_ids(args: Iterable[str], *, mod_dir: Path) -> list[int]:
    item_ids: dict[int, None] = {}  # Retain insertion order

    for x in args:
        with suppress(ValueError):
            item_id = parse_item_id(x, mod_dir=mod_dir)
            item_ids[item_id] = None
            continue

        try:
            ids = _parse_item_ids_from_path(Path(x))
        except (FileNotFoundError, UnicodeDecodeError, ValueError):
            log.debug("Could not parse %r as modpack file", x, exc_info=True)
        else:
            log.debug("Parsed %r as modpack file with item IDs: %s", x, ids)
            item_ids |= dict.fromkeys(ids)
            continue

        raise CommandError(f"Could not parse item ID, URL, or modpack file: {x}")

    return list(item_ids)


def _parse_item_ids_from_path(path: Path) -> list[int]:
    item_ids: list[int] = []

    with path.open(encoding="utf-8") as f:
        for line in f:
            for m in WORKSHOP_URL_PATTERN.finditer(line):
                item_ids.append(int(m[1]))

    if not item_ids:
        raise ValueError(f"No workshop URLs found in {path}")

    return item_ids


def raise_for_status(response: HTTPResponse):
    if not 200 <= response.status < 300:
        raise HTTPError(f"Unexpected HTTP status code: {response.status}")


def remove_broken_links(dir: Path, *, dry_run: bool) -> None:
    paths = dir.iterdir()
    links = [link for link in paths if link.is_symlink()]
    broken = [link for link in links if not link.exists()]
    for link in broken:
        log.warning("Removing broken link: %s", link.name)
        if not dry_run:
            link.unlink()


if __name__ == "__main__":
    main()
