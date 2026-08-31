#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "请以 root 运行: sudo ./scripts/install-wsl.sh" >&2
  exit 1
fi

if [[ $(dpkg --print-architecture) != amd64 ]]; then
  echo "当前安装脚本仅支持 Ubuntu 24.04 x86_64 (amd64)。" >&2
  exit 1
fi

. /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 24.04 ]]; then
  echo "当前安装脚本仅支持 Ubuntu 24.04。" >&2
  exit 1
fi

SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL_DIR=/opt/mc-manager
FRP_VERSION=0.68.0
FRP_ARCHIVE_SHA256=3cf934477f4fb1ee9e19e49c31fb33f5ffe3283300076f59afad8b8ccf1e1621

apt-get update
apt-get install -y \
  ca-certificates curl fuse-overlayfs podman python3.12 python3.12-venv \
  rsync slirp4netns uidmap

getent group mcmanager >/dev/null || groupadd --system mcmanager
getent passwd mcmanager-api >/dev/null || \
  useradd --system --gid mcmanager --home-dir /nonexistent --shell /usr/sbin/nologin mcmanager-api
getent passwd mcmanager-worker >/dev/null || \
  useradd --system --gid mcmanager --home-dir /var/lib/mc-manager/podman-home \
    --shell /usr/sbin/nologin mcmanager-worker
usermod --home /var/lib/mc-manager/podman-home mcmanager-worker
getent passwd frp >/dev/null || \
  useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin frp

ensure_subid_range() {
  local file=$1
  local option=$2
  if ! grep -q '^mcmanager-worker:' "$file"; then
    local start
    start=$(awk -F: 'BEGIN {max=99999} {end=$2+$3-1; if (end>max) max=end} END {print max+1}' "$file")
    usermod "$option" "$start-$((start + 65535))" mcmanager-worker
  fi
}
ensure_subid_range /etc/subuid --add-subuids
ensure_subid_range /etc/subgid --add-subgids

install -o root -g root -m 0644 \
  "$SOURCE_DIR/deploy/sysctl/99-mc-manager-podman.conf" \
  /etc/sysctl.d/99-mc-manager-podman.conf
sysctl -q -p /etc/sysctl.d/99-mc-manager-podman.conf

install_frpc() {
  if command -v frpc >/dev/null 2>&1 && \
    [[ $(frpc --version) == "$FRP_VERSION" ]]; then
    return
  fi
  local temporary archive asset github_url mirror_url
  temporary=$(mktemp -d)
  archive="$temporary/frp.tar.gz"
  asset="frp_${FRP_VERSION}_linux_amd64.tar.gz"
  github_url="https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/${asset}"
  mirror_url="https://ghfast.top/${github_url}"
  if ! curl -4 -fL --retry 2 --connect-timeout 10 --max-time 180 \
    "$github_url" -o "$archive"; then
    curl -4 -fL --retry 3 --connect-timeout 10 --max-time 180 \
      "$mirror_url" -o "$archive"
  fi
  printf '%s  %s\n' "$FRP_ARCHIVE_SHA256" "$archive" | sha256sum -c -
  tar -xzf "$archive" -C "$temporary"
  install -o root -g root -m 0755 \
    "$temporary/frp_${FRP_VERSION}_linux_amd64/frpc" /usr/local/bin/frpc
  rm -rf "$temporary"
}
install_frpc

install -d -o root -g root -m 0755 "$INSTALL_DIR"
rsync -rlt --delete --exclude .git --exclude .venv --exclude var \
  "$SOURCE_DIR/" "$INSTALL_DIR/"
chown -R root:root "$INSTALL_DIR"
find "$INSTALL_DIR" -type d -exec chmod 0755 {} +
find "$INSTALL_DIR" -type f -exec chmod 0644 {} +
rm -rf "$INSTALL_DIR/.venv"
python3.12 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install "$INSTALL_DIR"

install -d -o root -g mcmanager -m 0750 /etc/mc-manager
if [[ ! -f /etc/mc-manager/mc-manager.env ]]; then
  install -o root -g mcmanager -m 0640 "$INSTALL_DIR/.env.example" /etc/mc-manager/mc-manager.env
  api_token=$("$INSTALL_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')
  sed -i "s|^MC_API_TOKEN=.*|MC_API_TOKEN=$api_token|" /etc/mc-manager/mc-manager.env
fi
install -d -o mcmanager-worker -g mcmanager -m 2770 /var/lib/mc-manager
install -d -o mcmanager-worker -g mcmanager -m 0700 \
  /var/lib/mc-manager/podman-home \
  /var/lib/mc-manager/podman-home/.config \
  /var/lib/mc-manager/podman-home/.config/containers
install -o mcmanager-worker -g mcmanager -m 0600 \
  "$INSTALL_DIR/deploy/containers/registries.conf" \
  /var/lib/mc-manager/podman-home/.config/containers/registries.conf
install -o mcmanager-worker -g mcmanager -m 0600 \
  "$INSTALL_DIR/deploy/containers/containers.conf" \
  /var/lib/mc-manager/podman-home/.config/containers/containers.conf
install -d -o root -g mcmanager -m 0750 /srv/mc-manager /srv/mc-manager/.staging
install -d -o mcmanager-api -g mcmanager -m 2750 \
  /srv/mc-manager/maps /srv/mc-manager/repository /srv/mc-manager/uploads \
  /srv/mc-manager/.staging/api
install -d -o mcmanager-worker -g mcmanager -m 2700 \
  /srv/mc-manager/games /srv/mc-manager/active /srv/mc-manager/backups \
  /srv/mc-manager/artifacts \
  /srv/mc-manager/.staging/worker
# Worker 只读仓库地图，用于创建持久游戏副本。
chmod 2750 /srv/mc-manager/maps
chmod 2750 /srv/mc-manager/repository
install -d -o root -g frp -m 0750 /etc/frp
if [[ ! -f /etc/frp/frpc.toml ]]; then
  install -o root -g frp -m 0640 "$INSTALL_DIR/deploy/frp/frpc.toml.example" /etc/frp/frpc.toml
fi

install -o root -g root -m 0644 "$INSTALL_DIR"/deploy/systemd/*.service /etc/systemd/system/
install -o root -g root -m 0644 "$INSTALL_DIR/deploy/systemd/mc-manager.target" /etc/systemd/system/
systemctl daemon-reload

echo "安装完成。请编辑 /etc/mc-manager/mc-manager.env、/etc/frp/frpc.toml，并创建 /etc/frp/client_token。"
echo "然后执行: systemctl enable --now frpc mc-manager.target"
