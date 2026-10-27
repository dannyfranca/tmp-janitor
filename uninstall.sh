#!/usr/bin/env bash
set -euo pipefail

purge=0
for arg in "$@"; do
  case $arg in
    --purge) purge=1 ;;
    *) echo "Usage: uninstall.sh [--purge]" >&2; exit 2 ;;
  esac
done

unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}/tmp-janitor
state_dir=${XDG_STATE_HOME:-$HOME/.local/state}/tmp-janitor

for name in tmp-janitor-sweep tmp-janitor-alert; do
  systemctl --user disable --now "$name.timer" 2>/dev/null || true
  if ! systemctl --user stop "$name.service"; then
    service_state=$(systemctl --user show "$name.service" --property=ActiveState --value) || exit 1
    if [[ $service_state != inactive && $service_state != failed ]]; then
      echo "could not stop $name.service (state: $service_state)" >&2
      exit 1
    fi
  fi
  rm -f "$unit_dir/$name.service" "$unit_dir/$name.timer"
done
rm -f "$HOME/.local/bin/tmp-janitor"
systemctl --user daemon-reload

if ((purge)); then
  rm -rf "$config_dir" "$state_dir"
  echo "removed config and state: $config_dir $state_dir"
else
  echo "kept config: $config_dir (use --purge to remove it)"
fi
