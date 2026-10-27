# tmp-janitor

On many Linux systems, `/tmp` is a tmpfs. A tmpfs keeps its files in RAM. A file in `/tmp` uses RAM until you delete it, also after the process that wrote it stops. Build dirs, caches, and downloads in `/tmp` can use all the RAM, and then the system stops processes.

This tool does two jobs from systemd user timers:

| Job | Default schedule | What it does |
| --- | --- | --- |
| Sweep | Daily | Removes top-level entries in the configured dirs when nothing in them changed for the idle time. |
| Alert | Every 10 minutes | Sends a desktop notification when the file system of a path is too full. It removes nothing. |

If you set `XDG_CONFIG_HOME`, `~/.config` in this README means that dir. Set the same value when you run `install.sh` and `uninstall.sh`, and in the systemd user manager.

## Requirements

- Linux with systemd
- Python 3.12 or later
- `notify-send` (libnotify) for the alert

## Install

```sh
git clone https://github.com/dannyfranca/tmp-janitor.git
cd tmp-janitor
./install.sh
```

The installer does these steps:

1. It reads the schedules from your config, or from `config.example.toml` if you have no config. It checks each schedule with `systemd-analyze calendar`. If the config or a schedule is not valid, it stops before it changes files.
2. It copies the script to `~/.local/bin/tmp-janitor`.
3. It creates `~/.config/tmp-janitor/config.toml` from `config.example.toml`. If this file exists, it does not change it.
4. It writes the service and timer units to `~/.config/systemd/user/`, then enables and starts the timers.

To update, pull the repo and run `./install.sh` again.

## Configure

Edit `~/.config/tmp-janitor/config.toml`. The script reads it at each run. The schedules are in the timer units, so after you change a schedule, run `./install.sh` again.

```toml
[sweep]
schedule = "daily"
dry_run = false
keep = ["claude-*", "codex-*", "tmux-*", "systemd-private-*"]

[[sweep.dirs]]
path = "/tmp"
idle = "24h"

[[sweep.dirs]]
path = "~/.cache/agent-tmp"
idle = "7d"

[alert]
schedule = "*:0/10"
path = "/tmp"
percent = 75
```

| Key | Meaning |
| --- | --- |
| `sweep.schedule` | When the sweep runs. A systemd `OnCalendar` expression, or a list of them. Examples: `"hourly"`, `"*-*-* 11:15"`, `"Mon,Thu 03:00"`, `["06:00", "18:00"]`. |
| `sweep.dry_run` | `true` shows what a sweep would remove, without removing it. |
| `sweep.keep` | Top-level names that the sweep always keeps. Shell glob patterns. |
| `sweep.dirs` | The dirs to sweep. Add one `[[sweep.dirs]]` table for each dir. |
| `sweep.dirs.path` | An absolute path. `~` and `$VAR` expand. |
| `sweep.dirs.idle` | Remove an entry when nothing in it changed for this time. Units: `s`, `min`, `h`, `d`, `w`. You can combine them, for example `"1d12h"`. |
| `alert` | Remove this section to turn off the alert. |
| `alert.schedule` | When the alert check runs. Same format as `sweep.schedule`. |
| `alert.path` | The check uses the file system of this path. |
| `alert.percent` | Send a notification when the file system is this full, in percent. |

To use a different file for a manual run, set `TMP_JANITOR_CONFIG`. The installer and the timers use only `~/.config/tmp-janitor/config.toml`. If the file is missing, or has an unknown key or a value that is not valid, the command stops with exit code 2.

### Sweep rules

The sweep examines each top-level entry in each dir. It keeps the entry if one of these conditions is true:

- The name matches a `keep` pattern.
- A file or dir in the entry changed during the idle time. The sweep uses the newer of the modification time and the status change time, because archive tools keep old modification times on files that they extract.
- The entry contains a socket. A socket shows a runtime dir that a server uses.
- The entry contains a mount point. The removal would delete the files of the mounted file system.
- A process uses a path in the entry as its working dir, executable, open file, or mapped file.
- Another user owns the entry, or the entry is not a dir, a file, or a symbolic link.

The sweep removes a symbolic link, not its target. It also removes read-only trees, for example Go module caches. It does not sweep `/` or your home dir. Just before it removes an entry, the sweep examines the change time, the mounts, and the processes again.

### Agent temp dir

AI coding agents often put large build output in `/tmp`. To move that output to the disk, set `TMPDIR` for the agents to a dir on the disk, for example `~/.cache/agent-tmp`, and add that dir to `sweep.dirs`.

## Use

```sh
tmp-janitor sweep --dry-run   # show what a sweep would remove
tmp-janitor sweep             # sweep now
tmp-janitor alert             # check now, and notify if the path is too full
tmp-janitor schedule sweep    # show the sweep schedule from the config
```

If `~/.local/bin` is not in your `PATH`, use `~/.local/bin/tmp-janitor`.

Exit codes: 0 when the command is successful, 1 when a removal or a notification fails, 2 when the config is not valid.

The alert updates one notification. It does not add a new notification at each check.

### Use with systemd

```sh
systemctl --user start tmp-janitor-sweep.service                      # sweep now, through systemd
systemctl --user list-timers tmp-janitor-sweep.timer tmp-janitor-alert.timer   # next and last runs
journalctl --user -u tmp-janitor-sweep.service                        # logs of the sweeps
```

If the computer is off at the sweep time, the missed sweep runs when your systemd user manager next starts, usually at login. The alert does not run missed checks.

## Uninstall

```sh
./uninstall.sh           # keeps your config
./uninstall.sh --purge   # also removes ~/.config/tmp-janitor and ~/.local/state/tmp-janitor
```

The uninstaller disables the timers and stops the services. Then it removes the units and the script. If a service continues to run, the uninstaller keeps the files and exits with an error.

## Test

Run `python3 -m unittest discover -s tests`. The tests use temporary dirs and stub commands. They do not change your system. The systemd unit test needs `systemd-analyze`, and it is skipped without it.
