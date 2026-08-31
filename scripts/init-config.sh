#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONFIG_DIR="$SOURCE_DIR/config"
ENV_FILE="$CONFIG_DIR/mc-manager.env"
FRPC_FILE="$CONFIG_DIR/frpc.toml"

umask 077
mkdir -p "$CONFIG_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$SOURCE_DIR/.env.example" "$ENV_FILE"
  api_token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  sed -i "s|^MC_API_TOKEN=.*|MC_API_TOKEN=$api_token|" "$ENV_FILE"
  echo "已创建 config/mc-manager.env，并生成随机管理 Token。"
else
  echo "config/mc-manager.env 已存在，保持不变。"
fi

if [[ ! -f "$FRPC_FILE" ]]; then
  cp "$SOURCE_DIR/deploy/frp/frpc.toml.example" "$FRPC_FILE"
  echo "已创建 config/frpc.toml。"
else
  echo "config/frpc.toml 已存在，保持不变。"
fi

chmod 0600 "$ENV_FILE" "$FRPC_FILE"
echo "请直接用 VS Code 编辑 config/ 中的两个文件。"
echo "然后运行: bash scripts/build-frontend.sh"
echo "最后运行: sudo bash scripts/install-wsl.sh"