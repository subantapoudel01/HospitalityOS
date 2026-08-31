#!/usr/bin/env bash
#
# Prepare a fresh Ubuntu 22.04/24.04 VPS to host HospitalityOS.
# Run ONCE, as root or with sudo, on a server you have just created:
#
#   ssh root@<ip> 'bash -s' < infra/scripts/bootstrap-server.sh
#
# What it does: installs Docker, creates a non-root deploy user, turns on
# the firewall, and enables unattended security updates.
#
# WHAT IT DOES NOT DO: create the server, buy the domain, or point DNS at
# it. Those need your provider account and are listed in docs/DEPLOYMENT.md.

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-hospitality}"

[ "$(id -u)" -eq 0 ] || { echo "run as root or with sudo" >&2; exit 1; }

echo "==> System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg ufw unattended-upgrades

echo "==> Docker"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

echo "==> Deploy user: ${DEPLOY_USER}"
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi
# Docker group membership is effectively root on this host. That is the
# accepted trade for a single-purpose box; do not add anyone else to it.
usermod -aG docker "$DEPLOY_USER"

# Carry root's authorised keys over so you can log in as the deploy user.
if [ -f /root/.ssh/authorized_keys ]; then
  install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
  install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
    /root/.ssh/authorized_keys "/home/$DEPLOY_USER/.ssh/authorized_keys"
fi

echo "==> Firewall"
# Only these three. Postgres (5432) and Redis (6379) are deliberately
# absent: docker-compose.prod.yml publishes no ports for them, and the
# firewall is the second layer in case someone later adds one.
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Unattended security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Application directory"
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" /opt/hospitalityos

cat <<EOF

Bootstrap complete.

WARNING: Docker publishes ports by writing iptables rules that BYPASS ufw.
A container started with "-p 5432:5432" is reachable from the internet even
though ufw does not list it. The production compose file publishes only
80 and 443 for exactly this reason - check any change to it.

Next, as ${DEPLOY_USER}:

  git clone https://github.com/subantapoudel01/HospitalityOS.git /opt/hospitalityos
  cd /opt/hospitalityos
  cp .env.prod.example .env.prod
  \$EDITOR .env.prod
  ./infra/scripts/deploy.sh

Point an A record for your domain at this server BEFORE deploying;
Let's Encrypt verifies over HTTP and fails without it.
EOF
