#!/usr/bin/env bash
# Build the plugin and deploy it to a SteamOS device over SSH, then restart Decky.
#
# Usage: DECK_HOST=192.168.1.50 [DECK_USER=deck] bash scripts/deploy.sh
#
# The plugins directory is owned by root, so the remote step runs with sudo
# and asks for the device user's password (set it with `passwd` on the device).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${DECK_HOST:?Set DECK_HOST to the device IP or hostname}"
USER_NAME="${DECK_USER:-deck}"

# `ip addr` prints addresses as 192.168.1.50/24. With the /24 left in, rsync
# sees a slash before the colon and treats the target as a LOCAL path, so it
# would silently copy the build into ./deck@192.168.1.50/24:/tmp/...
if [[ "$HOST" == */* ]]; then
  echo "Note: dropping the subnet suffix from DECK_HOST ($HOST -> ${HOST%%/*})"
  HOST="${HOST%%/*}"
fi
if [[ -z "$HOST" || "$HOST" == *[[:space:]@]* ]]; then
  echo "DECK_HOST must be a plain IP address or hostname, got: '$DECK_HOST'" >&2
  exit 1
fi
NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$ROOT/plugin.json")"

bash "$ROOT/scripts/package.sh"

# Share one SSH connection between all steps so the SSH password is asked once.
CONTROL="$(mktemp -u "${TMPDIR:-/tmp}/eclipse-ssh.XXXXXX")"
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$CONTROL" -o ControlPersist=120)
trap 'ssh "${SSH_OPTS[@]}" -O exit "$USER_NAME@$HOST" 2>/dev/null || true' EXIT

echo "Connecting to $USER_NAME@$HOST (SSH password, if you use one)..."
ssh "${SSH_OPTS[@]}" "$USER_NAME@$HOST" true

echo "Uploading..."
rsync -az --delete -e "ssh ${SSH_OPTS[*]}" "$ROOT/out/$NAME/" "$USER_NAME@$HOST:/tmp/$NAME-deploy/"

# One terminal session for every sudo call: sudo caches the password per tty,
# so it is asked exactly once. A failed check prints the usual SteamOS causes.
ssh "${SSH_OPTS[@]}" -t "$USER_NAME@$HOST" "
  if ! sudo -v -p '[sudo] password for $USER_NAME on the device: '; then
    echo
    echo 'sudo failed. Common causes on SteamOS:'
    echo '  * Too many wrong attempts: pam_faillock locks the account for ~10 minutes,'
    echo '    during which even the correct password is rejected. Wait, then retry once.'
    echo '  * The password was set with an input method (e.g. a Vietnamese IME) active,'
    echo '    so it differs from what you type now. Reset it with passwd on the device.'
    rm -rf /tmp/$NAME-deploy
    exit 1
  fi
  set -e
  sudo rsync -a --delete /tmp/$NAME-deploy/ \"\$HOME/homebrew/plugins/$NAME/\"
  sudo systemctl restart plugin_loader
  rm -rf /tmp/$NAME-deploy
  echo 'Deployed. Decky is restarting.'
"
