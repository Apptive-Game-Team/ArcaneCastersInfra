#!/usr/bin/env bash
#
# One-time host preparation for a small game host. Run as root:
#
#   sudo ./bootstrap.sh <login-user>
#
# Installs Docker Engine from Docker's apt repository, adds a 2 GB swap file,
# lets <login-user> run docker without sudo, and installs game-deploy.timer,
# which runs rolling-deploy.sh every minute. Safe to run twice.

set -euo pipefail

user="${1:?usage: bootstrap.sh <login-user>}"
swap_file=/swapfile
swap_gb="${SWAP_GB:-2}"

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 1
fi

if ! swapon --show=NAME --noheadings | grep -qx "$swap_file"; then
    if [ ! -f "$swap_file" ]; then
        fallocate -l "${swap_gb}G" "$swap_file"
        chmod 600 "$swap_file"
        mkswap "$swap_file" >/dev/null
    fi
    swapon "$swap_file"
    grep -q "^${swap_file} " /etc/fstab || echo "${swap_file} none swap sw 0 0" >> /etc/fstab
fi

if ! command -v docker >/dev/null; then
    apt-get update
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

dir="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cat > /etc/systemd/system/game-deploy.service <<UNIT
[Unit]
Description=Replace the game container when its image changes
After=docker.service

[Service]
Type=oneshot
User=${user}
ExecStart=${dir}/rolling-deploy.sh
UNIT
cat > /etc/systemd/system/game-deploy.timer <<UNIT
[Unit]
Description=Poll for a new game image

[Timer]
OnBootSec=1min
OnUnitInactiveSec=${POLL_SECONDS:-60}s

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now game-deploy.timer

usermod -aG docker "$user"
systemctl enable --now docker
echo "done. ${user} must log in again for the docker group to apply."
