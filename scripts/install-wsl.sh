#!/usr/bin/env bash
set -euo pipefail
umask 077

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
LOCAL_CONFIG_DIR="$SOURCE_DIR/config"
DEPLOYED_CONFIG_DIR="$INSTALL_DIR/config"
FRP_VERSION=0.68.0
FRP_ARCHIVE_SHA256=3cf934477f4fb1ee9e19e49c31fb33f5ffe3283300076f59afad8b8ccf1e1621

frontend_source_fingerprint=$(
  cd "$SOURCE_DIR/frontend"
  find \
    index.html package.json package-lock.json vite.config.ts \
    tsconfig.json tsconfig.app.json tsconfig.node.json src public \
    -type f -print0 |
    sort -z |
    xargs -0 sha256sum |
    sha256sum |
    cut -d' ' -f1
)
frontend_build_fingerprint=$(cat \
  "$SOURCE_DIR/src/mc_manager/static/.mc-manager-frontend-source.sha256" \
  2>/dev/null || true)
if [[ ! -s "$SOURCE_DIR/src/mc_manager/static/index.html" ]] || \
  [[ $frontend_source_fingerprint != "$frontend_build_fingerprint" ]]; then
  echo "前端构建产物缺失或已过期，请先运行: bash scripts/build-frontend.sh" >&2
  exit 1
fi

APT_PACKAGES=(
  ca-certificates curl fuse-overlayfs podman python3.12 python3.12-venv
  rsync slirp4netns uidmap
)
missing_packages=()
for package in "${APT_PACKAGES[@]}"; do
  if ! dpkg-query -W -f='${db:Status-Abbrev}' "$package" 2>/dev/null | grep -q '^ii '; then
    missing_packages+=("$package")
  fi
done
if (( ${#missing_packages[@]} > 0 )); then
  echo "安装缺失的系统包: ${missing_packages[*]}"
  apt-get update
  apt-get install -y "${missing_packages[@]}"
else
  echo "系统依赖已安装，跳过 apt-get update/install。"
fi

getent group mcmanager >/dev/null || groupadd --system mcmanager
getent passwd mcmanager-api >/dev/null || \
  useradd --system --gid mcmanager --home-dir /nonexistent --shell /usr/sbin/nologin mcmanager-api
getent passwd mcmanager-worker >/dev/null || \
  useradd --system --gid mcmanager --home-dir /var/lib/mc-manager/podman-home \
    --shell /usr/sbin/nologin mcmanager-worker
usermod --home /var/lib/mc-manager/podman-home mcmanager-worker
getent passwd frp >/dev/null || \
  useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin frp

# 实际配置保存在 Git 忽略的 config 中。首次升级旧安装时自动把 /etc 配置
# 迁移回项目目录，避免管理员重新填写；新安装则生成本地配置骨架和随机 API Token。
mkdir -p "$LOCAL_CONFIG_DIR"
if [[ ! -f "$LOCAL_CONFIG_DIR/mc-manager.env" ]] && \
  [[ -f /etc/mc-manager/mc-manager.env ]]; then
  cp --dereference /etc/mc-manager/mc-manager.env "$LOCAL_CONFIG_DIR/mc-manager.env"
  echo "已将旧后端配置迁移到 config/mc-manager.env。"
fi
if [[ ! -f "$LOCAL_CONFIG_DIR/frpc.toml" ]] && [[ -f /etc/frp/frpc.toml ]]; then
  cp --dereference /etc/frp/frpc.toml "$LOCAL_CONFIG_DIR/frpc.toml"
  echo "已将旧 frpc 配置迁移到 config/frpc.toml。"
fi
if [[ ! -f "$LOCAL_CONFIG_DIR/mc-manager.env" ]] || \
  [[ ! -f "$LOCAL_CONFIG_DIR/frpc.toml" ]]; then
  bash "$SOURCE_DIR/scripts/init-config.sh"
fi

inline_legacy_frpc_token() {
  local config_file=$1
  local token_file=""
  if ! grep -q '^auth\.tokenSource\.' "$config_file"; then
    return
  fi
  for candidate in \
    "$LOCAL_CONFIG_DIR/frpc.token" \
    /etc/frp/client_token \
    "$DEPLOYED_CONFIG_DIR/frpc.token"; do
    if [[ -f $candidate ]]; then
      token_file=$candidate
      break
    fi
  done
  if [[ -z $token_file ]]; then
    echo "旧 frpc.toml 使用独立 Token，但没有找到 Token 文件，无法自动迁移。" >&2
    exit 1
  fi
  python3 - "$config_file" "$token_file" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
token_path = Path(sys.argv[2])
token = token_path.read_text(encoding="utf-8").strip()
if not token:
    raise SystemExit("旧 frpc Token 为空，无法迁移")
lines = config_path.read_text(encoding="utf-8").splitlines()
if any(line.strip().startswith("auth.token =") for line in lines):
    raise SystemExit("frpc.toml 同时包含 auth.token 和 auth.tokenSource，请手工处理")
updated: list[str] = []
inserted = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("auth.tokenSource."):
        continue
    updated.append(line)
    if stripped.startswith("auth.method ="):
        updated.append(f"auth.token = {json.dumps(token)}")
        inserted = True
if not inserted:
    raise SystemExit("frpc.toml 缺少 auth.method，无法迁移 Token")
temporary = config_path.with_suffix(".toml.tmp")
temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
temporary.replace(config_path)
PY
  rm -f "$LOCAL_CONFIG_DIR/frpc.token"
  echo "已将旧独立 frpc Token 安全迁移到 config/frpc.toml 的 auth.token。"
}
inline_legacy_frpc_token "$LOCAL_CONFIG_DIR/frpc.toml"

config_owner_uid=${SUDO_UID:-$(stat -c '%u' "$SOURCE_DIR")}
config_owner_gid=${SUDO_GID:-$(stat -c '%g' "$SOURCE_DIR")}
chown "$config_owner_uid:$config_owner_gid" "$LOCAL_CONFIG_DIR" \
  "$LOCAL_CONFIG_DIR/mc-manager.env" \
  "$LOCAL_CONFIG_DIR/frpc.toml"
chmod 0600 \
  "$LOCAL_CONFIG_DIR/mc-manager.env" \
  "$LOCAL_CONFIG_DIR/frpc.toml"

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
  if [[ -x /usr/local/bin/frpc ]] && \
    [[ $(/usr/local/bin/frpc --version) == "$FRP_VERSION" ]]; then
    echo "frpc $FRP_VERSION 已安装，跳过下载。"
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
rm -rf "$INSTALL_DIR/tmp" "$INSTALL_DIR/credentials" "$INSTALL_DIR/secrets"
find "$INSTALL_DIR" -maxdepth 1 -type f -name '.env*' -delete
rsync -rlt --delete \
  --exclude /config \
  --exclude .git --exclude .venv --exclude var --exclude tmp \
  --exclude credentials --exclude secrets --exclude '*.key' \
  --exclude '*.p12' --exclude '*.pfx' --exclude '*.pkcs12' \
  --exclude '*.jks' --exclude '*.keystore' \
  --exclude '/.env*' \
  --exclude frontend/node_modules \
  "$SOURCE_DIR/" "$INSTALL_DIR/"
find "$INSTALL_DIR" \
  \( -path "$INSTALL_DIR/.venv" -o -path "$DEPLOYED_CONFIG_DIR" \) -prune -o \
  -exec chown root:root {} +
find "$INSTALL_DIR" \
  \( -path "$INSTALL_DIR/.venv" -o -path "$DEPLOYED_CONFIG_DIR" \) -prune -o \
  -type d -exec chmod 0755 {} +
find "$INSTALL_DIR" \
  \( -path "$INSTALL_DIR/.venv" -o -path "$DEPLOYED_CONFIG_DIR" \) -prune -o \
  -type f -exec chmod 0644 {} +

VENV_DIR="$INSTALL_DIR/.venv"
if [[ ! -x "$VENV_DIR/bin/python" ]] || \
  ! "$VENV_DIR/bin/python" -c \
    'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
  echo "创建 Python 3.12 生产虚拟环境。"
  rm -rf "$VENV_DIR"
  python3.12 -m venv "$VENV_DIR"
fi

source_fingerprint=$(
  cd "$SOURCE_DIR"
  find pyproject.toml README.md src/mc_manager migrations -type f \
    ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.pyo' -print0 |
    sort -z |
    xargs -0 sha256sum |
    sha256sum |
    cut -d' ' -f1
)
fingerprint_file="$VENV_DIR/.mc-manager-source.sha256"
installed_fingerprint=$(cat "$fingerprint_file" 2>/dev/null || true)
if [[ $source_fingerprint != "$installed_fingerprint" ]] || \
  ! "$VENV_DIR/bin/python" -c 'import mc_manager' 2>/dev/null; then
  echo "项目代码或依赖已变化，更新生产 Python 包。"
  "$VENV_DIR/bin/python" -m pip install \
    --disable-pip-version-check 'hatchling>=1.25'
  "$VENV_DIR/bin/python" -m pip install \
    --disable-pip-version-check --no-build-isolation "$INSTALL_DIR"
  printf '%s\n' "$source_fingerprint" > "$fingerprint_file.tmp"
  mv "$fingerprint_file.tmp" "$fingerprint_file"
else
  echo "生产 Python 包未变化，跳过 pip 安装。"
fi

ensure_env_default() {
  local file=$1
  local key=$2
  local value=$3
  if ! grep -q "^${key}=" "$file"; then
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}
ensure_env_default "$LOCAL_CONFIG_DIR/mc-manager.env" MC_RESOURCE_PACK_BASE_URL ""
ensure_env_default "$LOCAL_CONFIG_DIR/mc-manager.env" MC_MAX_RESOURCE_PACK_BYTES 262144000
api_token_count=$(grep -c '^MC_API_TOKEN=' "$LOCAL_CONFIG_DIR/mc-manager.env" || true)
api_token=$(sed -n 's/^MC_API_TOKEN=//p' "$LOCAL_CONFIG_DIR/mc-manager.env")
if [[ $api_token_count -ne 1 ]] || [[ ${#api_token} -lt 32 ]] || \
  [[ $api_token == replace-with-a-long-random-secret ]] || \
  [[ $api_token =~ [[:space:]] ]]; then
  echo "config/mc-manager.env 必须且只能包含一个至少 32 字符、无空白的 MC_API_TOKEN。" >&2
  echo "可删除该文件后重新运行 scripts/init-config.sh 生成随机 Token。" >&2
  exit 1
fi

install -d -o root -g root -m 0755 "$DEPLOYED_CONFIG_DIR"
install -o root -g mcmanager -m 0640 \
  "$LOCAL_CONFIG_DIR/mc-manager.env" "$DEPLOYED_CONFIG_DIR/mc-manager.env"
install -o root -g frp -m 0640 \
  "$LOCAL_CONFIG_DIR/frpc.toml" "$DEPLOYED_CONFIG_DIR/frpc.toml"
rm -f "$DEPLOYED_CONFIG_DIR/frpc.token"

# 旧路径仅作为兼容入口，真实运行配置统一位于 /opt/mc-manager/config。
install -d -o root -g mcmanager -m 0750 /etc/mc-manager
install -d -o root -g frp -m 0750 /etc/frp
rm -f /etc/mc-manager/mc-manager.env /etc/frp/frpc.toml /etc/frp/client_token
ln -s "$DEPLOYED_CONFIG_DIR/mc-manager.env" /etc/mc-manager/mc-manager.env
ln -s "$DEPLOYED_CONFIG_DIR/frpc.toml" /etc/frp/frpc.toml
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
install -o root -g root -m 0644 "$INSTALL_DIR"/deploy/systemd/*.service /etc/systemd/system/
install -o root -g root -m 0644 "$INSTALL_DIR/deploy/systemd/mc-manager.target" /etc/systemd/system/
systemctl daemon-reload

echo "安装完成。唯一配置源是: $LOCAL_CONFIG_DIR"
echo "直接用 VS Code 编辑该目录；修改后重新运行本脚本部署配置。"
echo "配置完成后执行: systemctl enable --now mc-manager.target"
