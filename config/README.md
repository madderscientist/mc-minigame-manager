# 实际部署配置

此目录保存当前机器真正使用的配置，但实际配置和 Token 均被 Git 忽略。

在项目根目录执行：

```bash
bash scripts/init-config.sh
```

该命令从以下 Git 示例创建实际文件，且不会覆盖已有配置：

- `.env.example` → `config/mc-manager.env`
- `deploy/frp/frpc.toml.example` → `config/frpc.toml`

直接用 VS Code 编辑这些实际文件。然后依次运行 `bash scripts/build-frontend.sh` 和
`sudo bash scripts/install-wsl.sh`。安装脚本会将配置以受限权限复制到
`/opt/mc-manager/config/`，供 systemd 服务读取。

frps Token 直接填写在 `config/frpc.toml` 的 `auth.token` 中，因此该文件必须保持私密。

从旧版升级且实际配置仍在 `/etc/mc-manager`、`/etc/frp` 时，不要先运行初始化脚本；先
运行统一前端构建脚本，再运行安装脚本。安装脚本会把旧配置和 Token 迁移到此目录，不需要
重新填写。

不要提交此目录中的实际配置、Token、备份或临时副本。