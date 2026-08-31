#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
FRONTEND_DIR="$SOURCE_DIR/frontend"
STATIC_ROOT="$SOURCE_DIR/src/mc_manager/static"
STATIC_INDEX="$SOURCE_DIR/src/mc_manager/static/index.html"
BUILD_FINGERPRINT_FILE="$STATIC_ROOT/.mc-manager-frontend-source.sha256"

if ! command -v node >/dev/null 2>&1; then
  echo "未找到 Node.js，请先安装 Node.js 26。" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "未找到 npm，请先安装 npm 11。" >&2
  exit 1
fi

node_version=$(node --version)
node_major=${node_version#v}
node_major=${node_major%%.*}
if [[ $node_major != 26 ]]; then
  echo "需要 Node.js 26，当前版本为 $node_version。" >&2
  exit 1
fi

npm_version=$(npm --version)
npm_major=${npm_version%%.*}
if (( npm_major < 11 )); then
  echo "需要 npm 11 或更高版本，当前版本为 $npm_version。" >&2
  exit 1
fi

cd "$FRONTEND_DIR"
frontend_source_fingerprint=$(
  find \
    index.html package.json package-lock.json vite.config.ts \
    tsconfig.json tsconfig.app.json tsconfig.node.json src public \
    -type f -print0 |
    sort -z |
    xargs -0 sha256sum |
    sha256sum |
    cut -d' ' -f1
)
dependency_fingerprint=$(
  {
    sha256sum package.json package-lock.json
    printf 'node=%s\nnpm=%s\n' "$node_version" "$npm_version"
  } | sha256sum | cut -d' ' -f1
)
fingerprint_file="node_modules/.mc-manager-dependencies.sha256"
installed_fingerprint=$(cat "$fingerprint_file" 2>/dev/null || true)

if [[ ! -d node_modules ]] || [[ $dependency_fingerprint != "$installed_fingerprint" ]]; then
  echo "前端依赖缺失或锁文件/Node 版本已变化，执行 npm ci。"
  npm ci --include=dev
  printf '%s\n' "$dependency_fingerprint" > "$fingerprint_file"
else
  echo "前端依赖未变化，跳过 npm ci。"
fi

echo "运行前端测试。"
npm test

echo "运行 TypeScript 类型检查。"
npm run typecheck

echo "构建生产前端。"
npm run build:vite

if [[ ! -s "$STATIC_INDEX" ]]; then
  echo "前端构建未生成 $STATIC_INDEX。" >&2
  exit 1
fi

STATIC_ROOT="$STATIC_ROOT" node <<'NODE'
const fs = require('node:fs')
const path = require('node:path')

const root = process.env.STATIC_ROOT
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8')
const references = [...html.matchAll(/(?:src|href)="([^"?#]+)(?:[?#][^"]*)?"/g)]
  .map((match) => match[1])
  .filter((value) => value.startsWith('/') && value !== '/')
if (!references.some((value) => value.endsWith('.js'))) {
  throw new Error('index.html 未引用 JavaScript 构建产物')
}
if (!references.some((value) => value.endsWith('.css'))) {
  throw new Error('index.html 未引用 CSS 构建产物')
}
for (const reference of references) {
  const relative = decodeURIComponent(reference).replace(/^\/+/, '')
  const target = path.resolve(root, relative)
  if (!target.startsWith(`${path.resolve(root)}${path.sep}`) || !fs.statSync(target).isFile()) {
    throw new Error(`index.html 引用的文件不存在: ${reference}`)
  }
}
NODE

printf '%s\n' "$frontend_source_fingerprint" > "$BUILD_FINGERPRINT_FILE"

echo "生产前端构建完成: $STATIC_INDEX"
