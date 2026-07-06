# tgm.py

![Example help message](/docs/images/help.png)

A zero-dependency, single-file workshop mod manager CLI for Arma 3.

Best suited for [LinuxGSM](https://linuxgsm.com/) or manual
[Dedicated Server](https://community.bistudio.com/wiki/Arma_3:_Dedicated_Server)
setups.
Can be used with SteamCMD on Windows, although not preferred.
Not recommended for Docker-based setups like [Pterodactyl](https://pterodactyl.io/).

Originally, this script existed as a [gist](https://gist.github.com/thegamecracks/f02d59c1ba12a45c2a2518b48c48834f)
for quite some time. However, its revisions page became very slow to load
due to GitHub always rendering diffs for 40+ revisions simultaneously.
As such, I've moved development to a proper repository here.

## Installation

Python 3.11 or higher is required.

To download the latest release on Linux:

```sh
curl -LsSOf https://raw.githubusercontent.com/thegamecracks/tgm/refs/heads/main/tgm.py && chmod u+x tgm.py
```

<!--
  -L: follow redirects
  -s: silent operation, no progress bar
  -S: show errors while silent
  -O: derive output file from URL
  -f: fail on (most) server errors
-->

Alternatively, you can go to the [Releases](https://github.com/thegamecracks/tgm/releases/latest)
page and manually download tgm.py from the list of assets at the bottom.

For LinuxGSM, the most convenient placement is next to your `arma3server`
script, just outside of your `serverfiles/` directory. For other setups,
you will likely need to configure your [Directories](#directories)
as you go through the Usage section.

## Usage

tgm.py provides a self-contained `help` command to guide users through
setup and usage. This may be removed in the future to consolidate
documentation.

### Summary

tgm.py is written to help manage workshop mods specifically for Arma 3.
This can perform the following tasks, either in sequence or one at a time:

1. Fetch workshop mods and collections
2. Execute SteamCMD commands to install workshop mods
3. Symlink workshop mods and bikeys
4. Apply common fixes for Arma 3 mods

Example usage:

```sh
tgm.py install 450814997 3514182772 my_modpack.html
tgm.py i --dry-run https://steamcommunity.com/workshop/filedetails/?id=3489945148
tgm.py update --all
tgm.py details
tgm.py remove @cba_a3 3514182772
```

### Authentication

For seamless integration with SteamCMD, we recommend that you log into SteamCMD
first to cache your credentials:

```sh
$ ./steamcmd.sh
Steam>login yoursteamuser
Cached credentials not found.

password: ****
Proceeding with login using username/password.
Logging in user 'yoursteamuser' [U:1:1234] to Steam Public...OK
Waiting for client config...OK
Waiting for user info...OK

Steam>quit
Unloading Steam API...OK
```

Afterwards, you can use arguments or environment variables to specify
the path to SteamCMD and your username login:

```sh
# ~/.bashrc:
export TGM_STEAMCMD=/path/to/steamcmd.sh
export TGM_STEAM_USER=yoursteamuser
# Or CLI arguments:
tgm.py --steamcmd /path/to/steamcmd.sh --steamcmd-user yoursteamuser install ...
INSTALL: 450814997  (CBA_A3)
/path/to/steamcmd.sh +login yoursteamuser +workshop_download_item 107410 450814997 +quit
```

If you don't want tgm.py to run SteamCMD at all, pass the `-n/--dry-run` flag:

```sh
tgm.py install --dry-run ...
```

### Dry runs

Most commands have side effects such as installing mods and symlinking files.
To test commands without making any changes, you can use the `-n/--dry-run` flags:

```sh
tgm.py install -n 450814997
tgm.py update -n --all
tgm.py remove -n https://steamcommunity.com/workshop/filedetails/?id=3489945148
tgm.py lowercase -n
tgm.py link-mods -n
tgm.py link-keys -n
```

This prints most operations that the command will perform, but will avoid
enacting filesystem changes. Some operations may not be printed until the
actual run when side effects are known, such as symlinking newly installed mods.

### Directories

tgm.py has three important directories that it uses:

1. The workshop directory (current: \[system-dependent\])
2. The mod directory (current: \[system-dependent\])
3. The key directory (current: \[system-dependent\])

The workshop directory is where SteamCMD is expected to download mods to.
Subcommands like `fix-meta` and `lowercase` directly affect mod files here.

The mod directory is where mod symlinks are created. The `link-mods` subcommand
will generate symlinks to each mod in the workshop directory, like @cba_a3.

The key directory is where key symlinks are created. The `link-keys` subcommand
will generate symlinks to any keys in the mod directory, like cba_a3.bikey.

These directories can be customized using the following environment variables or options:

```sh
TGM_WORKSHOP_DIR= OR tgm.py --workshop-dir ...
TGM_MOD_DIR=      OR tgm.py --mod-dir ...
TGM_KEY_DIR=      OR tgm.py --key-dir ...
```

### Fixes

The following fixes and utilities are defined:

- `fix-acf`:
  Fix SteamCMD ACF metadata when mods are removed.
  This prevents SteamCMD from re-downloading removed workshop mods.

- `fix-meta`:
  Add publishedid= to meta.cpp files to assist in automatic
  downloads when using the Arma 3 Launcher and `verifySignatures=2`.

- `link-keys`:
  Symlink .bikey files from the mod directory to the keys directory.

- `link-mods`:
  Symlink mods from the workshop directory to the mod directory.

- `lowercase`:
  Lowercase PBO files to help mods load on Linux servers.
  (obsolete since Arma 3 v2.22)

Commands like install, update, and remove will automatically invoke these
utilities after completion. To disable this, use the `--no-fix` flag:

```sh
tgm.py install --no-fix 450814997
tgm.py update --all --no-fix
tgm.py remove --no-fix @cba_a3
```

### Items

For the install, update, and remove commands, the following formats
can be used to provide workshop mods:

1. Bare IDs (450814997)
2. Workshop URLs (https://steamcommunity.com/workshop/filedetails/?id=3489945148)
3. Mod symlinks (@cba_a3)
4. Text files (path/to/modpack.html)

Multiple items can be specified at once in any format:

```sh
tgm.py install 450814997 my_modpack.html
tgm.py update my_modpack.html https://steamcommunity.com/workshop/filedetails/?id=3489945148
tgm.py remove @cba_a3 @warriors_haven_framework
```

Workshop collections are automatically expanded.

Items that cannot be fetched from the Steam Web API, such as unlisted or deleted mods,
will be ignored by default. To raise these as errors, use the `--api-strict` flag:

```sh
tgm.py --api-strict install ...
```

Due to limitations with the Steam Web API, dependencies listed on items
cannot be automatically fetched.

### Details

To view a list of all installed mods, use the details subcommand:

```sh
tgm.py details
```

This will fetch every mod from the Steam Web API to show their real titles
and update timestamps. To skip this and only show the URLs and file paths,
use the `--no-fetch` flag:

```sh
tgm.py details --no-fetch
```

## Example dry run install

![Example dry run install](/docs/images/install-dry-run.png)

## License

This project is written under the [MIT License](/LICENSE).
