# 实际部署配置

此目录保存当前机器真正使用的配置，但实际配置和 Token 均被 Git 忽略。

在项目根目录执行：

```bash
bash scripts/init-config.sh
```

该命令从以下 Git 示例创建主配置，且不会覆盖已有配置：

- `.env.example` → `config/mc-manager.env`
- `deploy/frp/frpc.toml.example` → `config/frpc.toml`

直接用 VS Code 编辑这些实际文件。然后依次运行 `bash scripts/build-frontend.sh` 和
`sudo bash scripts/install-wsl.sh`。安装脚本会将配置以受限权限复制到
`/opt/mc-manager/config/`，供 systemd 服务读取。

可以在此目录增加更多以 `frpc` 开头、以 `.toml` 结尾的普通文件，例如
`frpc-resources.toml` 或 `frpc-admin.toml`。安装脚本会发现所有直属的 `frpc*.toml`，逐个
校验和部署，并为每个配置运行独立的 frpc 进程；删除配置后再次安装会同时停止并清理对应
进程。子目录中的文件不会被发现。

`config/frpc.toml` 是必须保留的主配置：它对应 `frpc.service`，后端环境中的
`MC_PUBLIC_GAME_HOST`、`MC_PUBLIC_GAME_PORT_MIN` 以及前端显示的游戏连接地址都应与它
保持一致。额外配置对应 `frpc@.service` 实例，不参与前端地址展示。

frps Token 直接填写在各自配置的 `auth.token` 中，因此所有 `frpc*.toml` 都必须保持私密。
多个进程不能重复使用同一个本地 `webServer.port`，不同 frps 上的相同远端端口则互不冲突。

从旧版升级且实际配置仍在 `/etc/mc-manager`、`/etc/frp` 时，不要先运行初始化脚本；先
运行统一前端构建脚本，再运行安装脚本。安装脚本会把旧配置和 Token 迁移到此目录，不需要
重新填写。

不要提交此目录中的实际配置、Token、备份或临时副本。

`MC_DEFAULT_OPERATORS_JSON` 配置所有游戏的默认 4 级 OP 名单，格式为
`{"Java玩家名":"正版UUID"}`。Worker 会在每次启动 Paper 前合并名单，不会删除地图已有
管理员。玩家改名后应更新键名，UUID 保持不变。