#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")" && pwd)
bin_dir=$HOME/.local/bin
unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}/tmp-janitor

if [[ $(uname) != Linux ]]; then
  echo "tmp-janitor needs Linux with systemd" >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
  echo "tmp-janitor needs Python 3.12 or later" >&2
  exit 1
fi

config=$config_dir/config.toml
[[ -f $config ]] || config=$repo/config.example.toml

# systemd cannot read the config, so the installer copies the schedules into the timers.
# It reads and checks them before it changes files, so a bad config stops the install early.
read_schedule() {
  local output
  if ! output=$(TMP_JANITOR_CONFIG=$config python3 "$repo/bin/tmp-janitor" schedule "$1"); then
    echo "could not load config: $config" >&2
    exit 1
  fi
  printf '%s' "$output"
}

on_calendar_lines() {
  local expression lines=""
  while IFS= read -r expression; do
    [[ -n $expression ]] || continue
    if ! systemd-analyze calendar "$expression" >/dev/null; then
      echo "invalid $1 schedule in $config: $expression" >&2
      exit 1
    fi
    lines+="OnCalendar=$expression"$'\n'
  done <<<"$2"
  printf '%s' "${lines%$'\n'}"
}

sweep_schedule=$(read_schedule sweep)
alert_schedule=$(read_schedule alert)
sweep_calendar=$(on_calendar_lines sweep "$sweep_schedule")
alert_calendar=$(on_calendar_lines alert "$alert_schedule")

install_timer() {
  local name=$1 calendar=$2 timer
  install -m644 "$repo/systemd/$name.service" "$unit_dir/$name.service"
  timer=$(<"$repo/systemd/$name.timer")
  printf '%s\n' "${timer//@ON_CALENDAR@/$calendar}" >"$unit_dir/$name.timer"
}

mkdir -p "$bin_dir" "$unit_dir"
install -m755 "$repo/bin/tmp-janitor" "$bin_dir/tmp-janitor"

if [[ -f $config_dir/config.toml ]]; then
  echo "keeping existing config: $config_dir/config.toml"
else
  mkdir -p "$config_dir"
  install -m644 "$repo/config.example.toml" "$config_dir/config.toml"
  echo "created config: $config_dir/config.toml"
fi

install_timer tmp-janitor-sweep "$sweep_calendar"
timers=(tmp-janitor-sweep.timer)
if [[ -n $alert_calendar ]]; then
  install_timer tmp-janitor-alert "$alert_calendar"
  timers+=(tmp-janitor-alert.timer)
else
  systemctl --user disable --now tmp-janitor-alert.timer 2>/dev/null || true
  rm -f "$unit_dir/tmp-janitor-alert.service" "$unit_dir/tmp-janitor-alert.timer"
  echo "no [alert] section: alert timer off"
fi

systemctl --user daemon-reload
systemctl --user enable "${timers[@]}"
# A running timer keeps its old next run time until it restarts.
systemctl --user restart "${timers[@]}"
systemctl --user list-timers "${timers[@]}" --no-pager
