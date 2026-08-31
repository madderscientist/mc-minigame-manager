# Minecraft 小游戏管理后端

运行于 Windows WSL2 的 Minecraft 小游戏控制平面，使用 Python、FastAPI、SQLite WAL、rootless Podman、systemd 和全局 frpc。

## 领域模型

- `Map / map_id`：仓库中的不可变原始地图，类似 image。
- `Game / game_id`：从 Map 创建的持久可玩副本，类似带持久卷的 container。
- `Run / run_id`：Game 的一次临时运行，仅供后端内部使用。
- `Backup / backup_id`：Game 内部有限时间线上的恢复点。
- `Task / task_id`：创建、启动、停止、恢复或删除的异步任务。

Map 与 Game 使用不同 ID 空间；API 不公开 `run_id`。

## 开发

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
# 开发时设置 MC_RUNTIME_BACKEND=fake
.venv/bin/mc-manager-init
.venv/bin/mc-manager-api
```

另一个终端运行 Worker：

```bash
.venv/bin/mc-manager-worker
```

质量检查：

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy src/mc_manager
```

完整 API、状态机、存储、Podman、frpc 和 WSL 部署说明见
[MC 小游戏管理后端设计与部署](docs/MC小游戏管理后端设计与部署.md)。
