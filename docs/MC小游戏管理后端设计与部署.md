# Minecraft 小游戏管理后端设计与部署

## 1. 架构结论

后端运行在 Windows 的 WSL2 Ubuntu 中：

- Python 3.12 项目内 venv；
- FastAPI 负责 HTTP API；
- 单 Worker 执行地图复制、Paper 下载、Podman 启停和备份；
- SQLite WAL 保存资源、任务、端口租约和状态机；
- 每次运行使用一个 rootless Podman 容器；
- 一个全局 frpc 静态注册全部端口池；
- systemd 管理 API、Worker 和 frpc；
- 数据全部位于 WSL ext4，不放在 `/mnt/c`。

```mermaid
flowchart TD
    Client --> API[FastAPI]
    API --> DB[(SQLite WAL)]
    Worker --> DB
    Worker --> Maps[Maps 不可变仓库]
    Worker --> Games[Games 持久可写副本]
    Worker --> Backups[Game 内部备份]
    Worker --> Podman[rootless Podman]
    Podman --> Paper[Paper Run]
    FRPC[全局 frpc] --> FRPS[公网 frps]
```

## 2. 领域模型

### Map

Map 是仓库中的不可变模板，类似容器 image：

- 只使用 `map_id`；
- 来源为上传的已有世界，或只含版本、生成参数和服务端配置的自然生成模板；
- 创建成功后不再修改内容；
- 保存 Minecraft 版本、DataVersion、Paper build、Java 主版本和制品校验信息；
- 保存受管的 `server.properties` 默认值；
- 一个 Map 可以创建任意多个互相独立的 Game。

自然生成 Map 本身不含 `level.dat`。它创建出的每个 Game 在第一次启动 Paper 时各自生成
世界；种子留空时各自随机，填写固定种子时可复现同一地形。

### Game

Game 是从 Map 创建出的持久可玩副本，类似 container 的持久工作区：

- 只使用 `game_id`；
- 通过 `map_id` 记录来源 Map；
- 创建 Game 只复制地图并进入可启动列表，不会启动 Paper、占用端口或连接 frp；
- 创建时可覆盖 Map 默认设置，合并后的最终配置作为 Game 快照持久化；
- 同一个 Game 可以反复启动和停止；
- 同一个 Game 同时最多有一个非终态 Run。

Map 与 Game 使用不同 ID 空间。数值偶然相同也不代表同一个资源，因为它们属于不同表和 API。

### Run

Run 是 Game 的一次临时运行：

- 使用内部 `run_id`；
- 持有 Podman 容器、端口、generation 和运行状态；
- API 不返回 `run_id`；
- Game 停止后 Run 进入历史状态，但 Game 及进度继续存在。

### Backup

Backup 是 Game 内部的有限线性时间线：

- 使用 UTC 微秒时间型 `backup_id`；
- 唯一键为 `(game_id, backup_id)`；
- backup 没有 `map_id` 或 `game_id` 之外的顶级身份；
- 默认每次正常停止创建一个备份；
- 加载备份前先创建当前状态的保护备份；
- 新备份提交成功后才异步回收窗口外旧文件。

### Task

Task 是耗时操作的取件码：

- 使用 `task_id`；
- 类型包括创建 Game、删除 Game、启动、停止和加载备份；
- 状态为 `pending → running → succeeded | failed | canceled`；
- API 通过 Task 返回当前步骤、进度、结果和脱敏错误；
- 创建命令支持 `Idempotency-Key`，用于安全重试。

## 3. 为什么只借鉴 Docker，而不直接使用 OCI layer 保存世界

概念映射如下：

| 系统对象 | 容器类比 | 实际实现 |
|---|---|---|
| Map | image | 不可变普通目录 |
| Game | container workspace/volume | Podman 外部持久目录 |
| Run | container process | rootless Podman 容器 |
| Backup | snapshot | 独立可校验恢复点 |
| Task | engine job | SQLite 持久任务 |

世界数据通过 bind mount 挂载到 `/data`，不写进容器 writable layer。因此容器可以随时删除和重建，不影响 Game。`podman commit` 不包含 bind mount，OCI layer 的 tar/whiteout 模型也不适合频繁修改的 Minecraft region 文件。

当前 WSL 数据盘是 ext4，不支持 reflink。存储演进路线是：

1. 当前使用完整目录复制，优先保证一致性；
2. 抽象 ActiveStore/BackupStore；
3. 引入 restic 风格内容寻址分块和异机副本；
4. 如需更低停服时间，再挂载独立 Btrfs VHDX，以子卷快照加速本地备份和恢复；
5. OverlayFS 只考虑用于延迟创建 Game，不作为备份格式，因为 `.mca` 文件首次写入可能整文件 copy-up。

物理存储可以使用增量图，但 API 始终保持 Game 内部的线性 Backup，不开放任意分支树。

## 4. API

所有接口使用 `/api`，暂不增加版本前缀。生产环境发送：

```http
Authorization: Bearer <MC_API_TOKEN>
```

创建或命令请求建议发送：

```http
Idempotency-Key: <客户端生成的唯一值>
```

### Map API

- `GET /api/maps`：列出全部可用仓库地图。
- `GET /api/paper/versions`：列出当前 Java 兼容表支持的 Paper 正式版本。
- `POST /api/maps/generated`：创建不含世界文件的自然生成 Map 模板。
- `POST /api/uploads/{upload_id}`：初始化或恢复分片上传会话。
- `PUT /api/uploads/{upload_id}/{kind}/{index}`：上传带 SHA-256 的单个分片。
- `POST /api/uploads/{upload_id}/complete`：校验全部分片并创建 Map。
- `DELETE /api/uploads/{upload_id}`：取消上传并清理会话。
- `POST /api/maps`：兼容旧客户端的 multipart 上传入口。
- `GET /api/maps/{map_id}`：查询指定 Map。
- `DELETE /api/maps/{map_id}`：删除没有关联 Game 的 Map。

自然生成请求示例：

```json
{
  "name": "随机速通",
  "mc_version": "1.21.11",
  "server_settings": {
    "gamemode": "survival",
    "difficulty": "hard",
    "spawn_protection": 0,
    "level_seed": null,
    "generate_structures": true,
    "custom": {}
  }
}
```

上传字段：

- `map` 或 `map.zip`：地图压缩包；
- `name`：名称；
- `mc_version`：可选回退值；后端优先读取地图 `level.dat` 的 `Data.Version.Name`，仅旧地图无法识别时要求填写；
- `paper_build`：可选的固定 Paper build；留空时优先复用仓库内同一 `mc_version` 最高的标准 build，没有匹配项才查询 PaperMC 最新稳定 build，并将精确编号固化到 Map；
- Java 主版本不由客户端提交，而是根据 Paper 官方兼容表从 `mc_version` 自动确定；
- `paper_url` 与 `paper_sha256`：可选的成对自定义制品；
- `resource_pack`：可选的单个玩家资源包 ZIP；
- `resource_pack_required`：玩家拒绝资源包时是否拒绝进入，默认 false；
- `resource_pack_prompt`：可选原生下载提示，最多 256 字符；
- `server_settings`：JSON 编码的受管服务端默认设置；上传已有世界时不允许提交种子、世界类型和结构生成参数；

受管设置包括出生点保护、游戏模式、难度、硬核、PVP、飞行、最大人数、白名单、视距、
模拟距离，以及自然生成所需的种子、世界类型和结构生成。还可提交经过属性名和控制字符
校验的自定义 `server.properties`。`server-port`、`level-name`、命令权限字段和
`resource-pack*` 始终由系统控制。当前不编辑 `paper-global.yml`、
`paper-world-defaults.yml`、`bukkit.yml` 或 `spigot.yml`。

玩家资源包必须在 ZIP 根目录包含有效 `pack.mcmeta`，压缩包最多 250 MiB。后端计算
Minecraft 协议使用的 SHA-1 和内部校验用 SHA-256，随后写入 `resource-pack`、
`resource-pack-sha1`、`require-resource-pack` 及可选 `resource-pack-prompt`。上传地图自带的
旧资源包 URL 会被清除，避免继承不受信任的外链。地图根目录存在 `resources.zip` 时会
自动将其作为玩家资源包；如果同时显式上传资源包，则使用显式上传版本。系统不自动合并
多个 ZIP。

下载端点为 `GET /resource-packs/maps/{map_id}/{sha1}/{filename}`。该端点故意位于
`/api` 外且不要求管理 Token，否则 Minecraft 客户端无法下载；响应使用内容哈希 URL、
一年 immutable 缓存、ETag 和 `nosniff`。除该下载路径外，管理 API 仍受 Bearer Token 保护。

管理前端按 8 MiB 分片、最多四路并发上传，每片单独校验 SHA-256；不同分片可并行落盘，
同一分片和完成操作通过文件锁互斥。刷新后选择同一文件会复用 `upload_id`，已存在分片可
幂等重传；完成结果也可重复读取。未完成会话 24 小时后清理，取消时立即清理；
`MC_MAX_UPLOAD_SESSIONS` 和 `MC_MAX_UPLOAD_RESERVED_BYTES` 还会限制全局并发会话与
逻辑预留空间。代理只需接受略大于 8 MiB 的单请求，不再需要放行 2 GiB 请求体。兼容 multipart 入口仍由 ASGI
中间件累计计数。上传完成后的解压、扫描与哈希在线程池中使用独立数据库 Session，不阻塞
API 事件循环。前端同时保存 `Idempotency-Key`；完成响应未知时复用同一会话和键，避免
重复创建 Map。

### Game API

- `GET /api/games`：列出全部持久 Game。
- `POST /api/games`：根据 JSON 中的 `map_id` 创建 Game，但不启动。
- `GET /api/games/{game_id}`：查询 Game、来源 Map、准备状态和最近运行状态。
- `DELETE /api/games/{game_id}`：异步删除已经停止的 Game 及其 Backup。

创建请求：

```json
{
  "map_id": 123,
  "name": "可选的本局名称",
  "server_settings": {
    "gamemode": "adventure",
    "spawn_protection": 0,
    "custom": {}
  }
}
```

`server_settings` 省略时完整继承 Map 默认值；传入时表示前端编辑后的完整最终快照，而不是
仅包含差异的补丁。启动接口不接受服务端设置，Worker 会在创建和每次启动时重新应用 Game
快照，并最后覆盖系统托管属性。

返回 HTTP 202：

```json
{
  "task_id": "uuid",
  "game_id": 456,
  "map_id": 123,
  "status": "pending"
}
```

### 运行命令

- `POST /api/start`：启动已有 Game。
- `POST /api/stop`：停止 Game，可选择成功停止后是否创建备份。
- `POST /api/load`：保护当前状态后，将 Backup 原地加载回同一个 Game。

启动请求：

```json
{
  "game_id": 456,
  "port": 30001
}
```

`port` 可省略，由端口池分配。返回 `task_id`、`game_id`、分配端口和状态，不公开内部 `run_id`。

停止请求：

```json
{
  "game_id": 456,
  "backup": true
}
```

`backup` 默认为 `true`；设为 `false` 时只停止 Game 并释放端口，不创建新的恢复点。

加载请求：

```json
{
  "game_id": 456,
  "backup_id": "20260831T170000123456Z"
}
```

### Backup API

- `GET /api/games/{game_id}/backups`：列出 Game 的内部备份。
- `DELETE /api/games/{game_id}/backups/{backup_id}`：逻辑删除备份，文件由 Worker 回收。

### Task 与状态

- `GET /api/tasks/{task_id}`：查询任务步骤、进度、结果或错误。
- `GET /api/status`：查询运行中的 Game、最近 Task 和端口池。
- `GET /healthz`：不鉴权的本机健康检查。

没有 `/api/list`、`/api/operations`、按端口 stop 或混合 Map/Game 的 start。GET/DELETE 的资源 ID 放 URL；业务命令参数统一放 JSON。

错误格式：

```json
{
  "error": {
    "code": "game_not_found",
    "message": "游戏不存在",
    "details": {}
  }
}
```

## 5. 工作流

### 创建 Game

1. API 验证 Map 并预分配 `game_id` 与 `task_id`；
2. Worker 将不可变 Map 复制到 staging；
3. 应用 Game 的最终服务端设置，校验后原子发布到 Game 目录；
4. Game 进入 `ready`；
5. 不启动容器，不分配端口。

### 启动 Game

1. 验证 Game 为 `ready` 且没有其他任务或运行；
2. 创建内部 Run；
3. 在 SQLite 短事务中预留端口并递增 generation；
4. 固定 Paper build、SHA-256 与 Java 镜像；
5. 重新应用 Game 设置和系统托管属性，下载并校验缺失的 Paper JAR；
6. rootless Podman 创建容器，将 Game 挂载到 `/data`；
7. 等待 Paper 日志 `Done (` 且 TCP 端口可连接；
8. 标记 Run ready、端口 active、Task succeeded。

### 停止 Game

1. 向 Paper 容器 PID 1 发送 SIGTERM；
2. 最多等待 120 秒自然退出；
3. 超时则 SIGKILL，并将备份标记为不干净；
4. 在 staging 创建完整 Backup 并校验；
5. 提交 Backup 和 Task；
6. 释放匹配 `run_id + generation` 的端口；
7. 回收超出上限的旧 Backup。

### 加载 Backup

1. 独占锁定 Game；
2. 如在运行则先停止；
3. 创建 `before_restore` 保护备份；
4. 校验目标 Backup；
5. 恢复到 staging；
6. 原子替换 Game 目录；
7. `game_id` 保持不变；
8. 加载完成后由客户端重新调用 start。

## 6. 数据库与恢复

最终表：

- `maps`
- `games`
- `backups`
- `runs`
- `tasks`
- `port_leases`

关键约束：

- `games.map_id` 外键指向 Map，存在 Game 时拒绝删除 Map；
- 每个 Game 同时最多一个非终态 Run；
- 每个 Run 同时最多一个 pending/running stop Task；
- 每个端口最多绑定一个 Run；
- `(game_id, backup_id)` 唯一；
- `Idempotency-Key` 全局唯一。
- Map 保存 `source_type` 和默认设置 JSON；Game 保存创建时固化的最终设置 JSON。

Alembic `20260831_0002` 可迁移旧的混合 Map/active 数据：旧 repository 行进入 `maps`，旧 active 行进入 `games`，旧 instance/operation 分别进入内部 `runs` 和公开 `tasks`。旧物理 `repository/active` 路径仍受 systemd 过渡权限支持；所有新资源使用 `maps/games`。

Worker 启动时：

- 单机进程锁阻止第二个 Worker；
- 所有旧 `running` Task 立即回到 pending；
- Game 锁保留到对应 Task 成功或失败，不因容器缺失提前解锁；
- 对账数据库 Run 与 Podman 容器；
- 释放确认不存在的 Run 的端口；
- 恢复中断的目录替换；
- 回收备份垃圾和无引用目录。

## 7. 目录与权限

```text
/srv/mc-manager/
├── maps/<map_id>/
├── games/<game_id>/
├── backups/<game_id>/<backup_id>/
├── artifacts/paper/<sha256>.jar
├── uploads/
└── .staging/
    ├── api/
    └── worker/
```

- API 用户可写 `maps`、`uploads` 和 API staging；
- Worker 可读 `maps`，可写 `games`、`backups`、`artifacts` 和 Worker staging；
- Paper 容器只读挂载 JAR、读写挂载一个 Game；
- Map 不挂载给 Paper；
- Backup 不挂载给 Paper；
- SQLite 位于 `/var/lib/mc-manager`，API 与 Worker 通过受限共享组访问。

## 8. Paper、Java 与 Podman

Java 主版本映射到固定容器镜像。SDKMAN 只适合管理员在交互式开发环境中测试 JDK，不参与 systemd 生产启动，因为其 shell 初始化、可变 candidate 和共享宿主环境不具备容器镜像的可复现性与隔离性。

Podman 容器使用：

- rootless 系统用户；
- 独立 subuid/subgid；
- `cap-drop=ALL`；
- `no-new-privileges`；
- 只读容器根；
- 临时 `/tmp`；
- CPU、内存和 PID 限制；
- 仅绑定 `127.0.0.1:<port>`；
- restart policy `no`。

当前 registry 配置优先 DaoCloud、再尝试 1ms、最后访问 Docker Hub。USTC Docker Hub 缓存已暂停。生产镜像应固定 digest。

## 9. frpc

frpc 0.68.0 作为单个 systemd 服务运行，并静态注册整个端口池：

```text
frps:30000 → frpc → WSL 127.0.0.1:30000 → Paper:25565
```

启动和停止 Game 不修改 frpc 配置。只有管理员调整整个端口池时才验证并 reload。`remotePort` 位于 frps 主机，必须同时配置 frps `allowPorts`、操作系统防火墙和云安全组。

后端通过 `MC_PUBLIC_GAME_HOST` 和 `MC_PUBLIC_GAME_PORT_MIN` 将本地租约端口换算为公网连接地址。两个端口范围必须等长，前端直接显示并允许复制后端返回的 `public_address`，不硬编码部署域名。

frpc 模板保存在 `deploy/frp/frpc.toml.example`。执行 `scripts/init-config.sh` 后，在项目
`config/frpc.toml` 中填写真实参数和 `auth.token`，再由安装脚本部署后启用。

### 玩家资源包公网下载

Paper 只把 `server.properties` 中的 URL 和 SHA-1 发送给玩家，不会把 WSL 本地 ZIP 直接
传给客户端。需要先在项目 `config/mc-manager.env` 配置：

```text
MC_RESOURCE_PACK_BASE_URL=https://packs.example.com
```

该 URL 必须从玩家网络可访问，不能是 `127.0.0.1`。可在全局 frpc 中额外把本机 8080
映射到 frps 的独立 HTTP 端口，再由公网已有的 HTTPS 反向代理仅将 `/resource-packs/`
转发到该端口；示例已注释在 `deploy/frp/frpc.toml.example`。修改后重新运行安装脚本同步
配置并重启 API，然后重新导入带资源包的 Map。已经导入的 Map 是不可变的，不会改写旧 URL。

可先从玩家所在网络打开 Map 详情中的“测试下载”。若下载失败，应先修复 DNS、HTTPS、
FRP、防火墙或反向代理，而不是关闭 SHA-1 校验。下载端点匿名公开，因此资源包中不得放置
任何密钥或私有文件。

## 10. WSL 部署

启用 systemd 后执行：

```bash
bash scripts/init-config.sh
# 直接编辑项目 config/mc-manager.env 和 config/frpc.toml
bash scripts/build-frontend.sh
sudo bash scripts/install-wsl.sh
```

初始化脚本根据 Git 中的 `.env.example` 和 `deploy/frp/frpc.toml.example` 生成被忽略的
项目 `config/`，并生成随机 API Token。安装脚本安装 Python venv、rootless Podman 组件、
固定校验的 frpc、服务账户、subuid/subgid、数据目录和 systemd 单元，并将实际配置部署到
`/opt/mc-manager/config/`。

配置：

- 示例：`.env.example`、`deploy/frp/frpc.toml.example`
- 实际唯一来源：`config/mc-manager.env`、`config/frpc.toml`
- systemd 读取的部署副本：`/opt/mc-manager/config/`

实际配置直接在项目中编辑，无需 `sudoedit`。修改后重新运行安装脚本同步受保护副本。
不让 systemd 直接读取用户 Home 下的项目文件，因为服务使用 `ProtectHome=true`，且生产
服务不应直接信任普通用户可随时改写的密钥文件。

首次安装尚未启用 `mc-manager.target` 时，安装脚本只部署文件和单元；target 已启用的升级
安装会自动执行 Alembic 迁移，并重启 API、Worker 和 frpc 以加载新代码与配置。因此不要
只重复执行 `systemctl enable --now`：已经运行的服务不会因为 enable 操作自动重启。

启用 API 与 Worker：

```bash
sudo systemctl enable --now mc-manager-api mc-manager-worker
```

frps 配置完成后：

```bash
sudo systemctl enable --now frpc
```

常用检查：

```bash
systemctl status mc-manager-api mc-manager-worker frpc
journalctl -u mc-manager-api -f
journalctl -u mc-manager-worker -f
curl http://127.0.0.1:8080/healthz
```

Windows 还需使用任务计划程序在无人登录的冷启动时唤醒指定 WSL 发行版。

## 11. 管理前端

管理台使用 Vue 3、Vite 和 TypeScript，生产环境不增加 Web 服务：FastAPI 在同一端口
提供 `/assets/*`、SPA 页面和 `/api/*`。前端所有请求使用相对 URL，因此无需 CORS。

管理台的 `/help` 内置保姆级教程，覆盖 Ubuntu/WSL 首次安装、systemd、后端环境变量、
全局 frpc、Map→Game→启动→停止→恢复的完整流程、地图压缩包要求和常见故障命令。
地图上传窗口也会直接展示结构、安全和容量检查清单，避免管理员必须先查外部文档。

一级页面只有概览、游戏、地图和任务：

- 概览：运行 Game、端口余量、进行中和失败 Task；
- 游戏：创建、启动、停止、删除，以及 Game 内部 Backup 时间线；
- 地图：上传已有世界或创建自然生成模板、配置默认服务端属性、查看运行版本、创建 Game 和安全删除；
- 任务：显示最近 Task 的中文步骤、进度、结果和脱敏错误。

`run_id` 不出现在界面。恢复 Backup 需要输入 `game_id` 二次确认；删除 Game 和 Backup
同样使用危险确认。Bearer Token 仅保存到当前标签页的 `sessionStorage`，401 会立即清除。

开发：

```bash
cd frontend
npm install
npm run dev
```

生产构建与检查：

```bash
bash scripts/build-frontend.sh
```

脚本会在依赖锁文件或 Node/npm 版本变化时执行 `npm ci`，然后依次运行测试、类型检查和
Vite 构建，并将产物直接输出到 `src/mc_manager/static/`。FastAPI 只有在入口文件存在时才注册
SPA fallback，因此没有前端产物的纯后端开发环境仍可启动。所有 `/api` 路径优先由 API
处理，不会被前端 fallback 吞掉。

## 12. 当前验证状态

已验证：

- 完整 pytest 流程；
- Ruff 与 mypy；
- Alembic 空库与 0001 非空迁移；
- ORM 与 migration schema 一致；
- rootless Podman Worker systemd 沙箱；
- Temurin Java 17 镜像运行；
- PaperMC v3 精确 build 元数据查询；
- frpc 0.68.0 配置校验；
- API Bearer 鉴权、健康检查及 systemd 启动。

尚需在真实地图和公网 frps 环境验收：Paper 完整启动、玩家连接、正常 SIGTERM 存档、frp 公网端口和 Windows 冷启动恢复。
