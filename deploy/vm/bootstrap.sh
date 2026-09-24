#!/usr/bin/env bash
# Prepares the mcpwr VM for production (spec section 5). Idempotent: re-run it after any change
# under deploy/ - the deploy agent does not update itself.
#
#   first run:  ssh mlai@10.21.36.20 'sudo bash -s -- --ref main' < deploy/vm/bootstrap.sh
#   later:      sudo /opt/mcpwr/src/backend-mcp/deploy/vm/bootstrap.sh --ref main [--enable-timers]
set -euo pipefail

# When run from the checkout it updates, re-exec from a copy first: bash reads scripts
# incrementally, and the `git checkout` below may rewrite this very file mid-run.
if [ -z "${MCPWR_BOOTSTRAP_COPY:-}" ] && [ -f "$0" ]; then
  copy=$(mktemp)
  cp "$0" "$copy"
  MCPWR_BOOTSTRAP_COPY="$copy" exec bash "$copy" "$@"
fi
if [ -n "${MCPWR_BOOTSTRAP_COPY:-}" ]; then trap 'rm -f "$MCPWR_BOOTSTRAP_COPY"' EXIT; fi

REPO="https://github.com/Solvro/backend-mcp.git"
HOME_DIR=/opt/mcpwr
SRC="$HOME_DIR/src/backend-mcp"
REF=main
ENABLE_TIMERS=0

usage() {
  cat >&2 <<'EOF'
usage: bootstrap.sh [--ref <branch|tag|sha>] [--enable-timers]
  --ref            what to install the agent, configs and units from (default: main)
  --enable-timers  also start the per-stack deploy timers and the nightly backup timer
EOF
  exit "${1:-2}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ref)
      [ $# -ge 2 ] || usage
      REF="$2"
      shift 2
      ;;
    --enable-timers)
      ENABLE_TIMERS=1
      shift
      ;;
    -h | --help) usage 0 ;;
    *) usage ;;
  esac
done

log() { printf '==> %s\n' "$*"; }
die() {
  printf 'bootstrap: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)"
# shellcheck source=/dev/null
. /etc/os-release
case "$ID" in ubuntu | debian) ;; *) die "unsupported OS: $ID (Debian/Ubuntu only)" ;; esac
export DEBIAN_FRONTEND=noninteractive

log "packages"
apt-get update -q
apt-get install -y -q ca-certificates curl git jq openssl ufw unattended-upgrades util-linux

if ! command -v docker >/dev/null 2>&1; then
  log "docker engine from Docker's apt repository"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$ID" "$VERSION_CODENAME" >/etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

log "docker daemon settings"
desired='{"live-restore": true, "log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "5"}}'
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ] ||
  [ "$(jq -S . /etc/docker/daemon.json)" != "$(printf '%s' "$desired" | jq -S .)" ]; then
  printf '%s' "$desired" | jq . >/etc/docker/daemon.json
  # Until live-restore is on, this restart also restarts running containers (unless-stopped).
  systemctl restart docker
fi
systemctl enable --now docker >/dev/null

log "unattended security upgrades that never reboot on their own"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
cat >/etc/apt/apt.conf.d/52mcpwr-no-reboot <<'EOF'
Unattended-Upgrade::Automatic-Reboot "false";
EOF

log "deploy user"
if ! id mcpwr-deploy >/dev/null 2>&1; then
  useradd --system --home-dir "$HOME_DIR" --no-create-home --shell /usr/sbin/nologin mcpwr-deploy
fi
usermod -aG docker mcpwr-deploy

log "directories"
install -d -o root -g root -m 0755 "$HOME_DIR" "$HOME_DIR/agent" "$HOME_DIR/agent/stacks" "$HOME_DIR/src"
install -d -o mcpwr-deploy -g mcpwr-deploy -m 0755 "$HOME_DIR/stacks" "$HOME_DIR/state" \
  "$HOME_DIR/stacks/backend" "$HOME_DIR/stacks/ml-mcp" "$HOME_DIR/stacks/frontend"
install -d -o mcpwr-deploy -g mcpwr-deploy -m 0700 "$HOME_DIR/.docker" # the docker CLI's config dir
install -d -o root -g mcpwr-deploy -m 0750 /etc/ml-mcp /etc/ml-mcp/secrets
install -d -o root -g root -m 0700 /var/backups/mcpwr

log "backend-mcp checkout at $REF"
if [ ! -d "$SRC/.git" ]; then git clone -q "$REPO" "$SRC"; fi
git -C "$SRC" fetch -q origin "$REF"
git -C "$SRC" checkout -q --detach FETCH_HEAD

log "agent, stack configs and systemd units"
install -o root -g root -m 0755 "$SRC/deploy/agent/mcpwr-deploy" "$HOME_DIR/agent/mcpwr-deploy"
install -o root -g root -m 0755 "$SRC/deploy/vm/backup.sh" "$HOME_DIR/agent/backup.sh"
install -o root -g root -m 0644 "$SRC"/deploy/agent/stacks/*.conf "$HOME_DIR/agent/stacks/"
install -o root -g root -m 0644 "$SRC"/deploy/systemd/* /etc/systemd/system/
systemctl daemon-reload

log "shared network solvro-mcp-internal"
docker network inspect solvro-mcp-internal >/dev/null 2>&1 ||
  docker network create --internal solvro-mcp-internal >/dev/null

log "ssh"
admin="${SUDO_USER:-mlai}"
admin_home=$(getent passwd "$admin" | cut -d: -f6 || true)
if [ -n "$admin_home" ] && [ -s "$admin_home/.ssh/authorized_keys" ]; then
  # 10- sorts before cloud-init's 50-cloud-init.conf, and sshd keeps the first value it reads.
  cat >/etc/ssh/sshd_config.d/10-mcpwr.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
  sshd -t
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
else
  log "WARNING: $admin has no ~/.ssh/authorized_keys - leaving SSH password login enabled"
fi

log "firewall"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw --force enable >/dev/null
# Docker-published ports (nginx :80) bypass ufw; limiting :80 to Coolify is spec follow-up F2.

if [ "$ENABLE_TIMERS" = 1 ]; then
  log "timers"
  for stack in ml-mcp backend frontend; do
    systemctl enable --now "mcpwr-deploy@$stack.timer"
  done
  systemctl enable --now mcpwr-backup.timer
fi

log "done"
cat <<EOF
Next:
  sudo $SRC/deploy/vm/init-secrets.sh          first time only; never overwrites
  sudo $SRC/deploy/vm/bootstrap.sh --ref $REF --enable-timers
  sudo -u mcpwr-deploy $HOME_DIR/agent/mcpwr-deploy backend status
EOF
