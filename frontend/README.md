# MC Manager Frontend

Vue 3 + Vite + TypeScript 管理台。

地图入口支持两个模式：上传包含 `level.dat` 的已有世界，或选择 Minecraft 版本创建自然生成
模板。地图保存服务端设置默认值，创建游戏时可以覆盖并固化最终配置；启动窗口只负责端口。

```bash
npm install
npm run dev
```

开发服务器将 `/api` 和 `/healthz` 代理到 `127.0.0.1:8080`。生产构建：

```bash
cd ..
bash scripts/build-frontend.sh
```

脚本会按锁文件指纹决定是否执行 `npm ci`，再运行测试、类型检查和 Vite 构建。产物写入
后端包的 `src/mc_manager/static/`，由 FastAPI 同源托管。
