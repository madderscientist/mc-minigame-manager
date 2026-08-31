<script setup lang="ts">
import MapRequirements from '../components/MapRequirements.vue'

const sections = [
  { id: 'install', label: '首次安装' },
  { id: 'config', label: '后端配置' },
  { id: 'frp', label: 'FRP 配置' },
  { id: 'usage', label: '使用流程' },
  { id: 'maps', label: '地图要求' },
  { id: 'troubleshooting', label: '故障排查' },
]

function copy(text: string) {
  void navigator.clipboard.writeText(text)
}
</script>

<template>
  <div class="page help-page">
    <header class="page-header">
      <div><div class="eyebrow">Step-by-step guide</div><h1>配置与使用教程</h1><p>从一台新 WSL 到上传地图、开服、停服和恢复备份。</p></div>
      <a class="button ghost" href="/docs" target="_blank">OpenAPI 文档 ↗</a>
    </header>

    <div class="help-layout">
      <aside class="help-toc panel">
        <strong>教程目录</strong>
        <a v-for="(section,index) in sections" :key="section.id" :href="`#${section.id}`"><span>0{{ index + 1 }}</span>{{ section.label }}</a>
      </aside>

      <div class="help-content">
        <section id="install" class="guide-section panel">
          <div class="guide-heading"><span>01</span><div><div class="eyebrow">One-time setup</div><h2>首次安装</h2></div></div>
          <ol class="step-list">
            <li><div class="step-number">1</div><div><h3>确认运行环境</h3><p>当前安装脚本支持 Windows WSL2、Ubuntu 24.04 x86_64。先确认 WSL 中的 PID 1 是 systemd。</p><div class="command"><code>ps -p 1 -o comm=</code><button @click="copy('ps -p 1 -o comm=')">复制</button></div></div></li>
            <li><div class="step-number">2</div><div><h3>启用 systemd</h3><p>如果上一步不是 <code>systemd</code>，将下面内容写入 <code>/etc/wsl.conf</code>，再从 Windows PowerShell 重启 WSL。</p><div class="command multiline"><code>[boot]
systemd=true

# Windows PowerShell:
wsl --shutdown</code><button @click="copy('[boot]\nsystemd=true')">复制配置</button></div></div></li>
            <li><div class="step-number">3</div><div><h3>初始化配置、构建并安装</h3><p>先生成被忽略的 <code>config/</code> 实际配置并直接编辑，再用统一脚本测试和构建前端，最后执行系统安装。</p><div class="command multiline"><code>bash scripts/init-config.sh
# 用 VS Code 编辑 config/ 后：
bash scripts/build-frontend.sh
sudo bash scripts/install-wsl.sh</code><button @click="copy('bash scripts/init-config.sh')">复制初始化命令</button></div></div></li>
            <li><div class="step-number">4</div><div><h3>启动 API 与 Worker</h3><div class="command"><code>sudo systemctl enable --now mc-manager-api mc-manager-worker</code><button @click="copy('sudo systemctl enable --now mc-manager-api mc-manager-worker')">复制</button></div><p>打开 <code>http://127.0.0.1:8080/</code>，输入安装时生成的 Token。</p></div></li>
          </ol>
        </section>

        <section id="config" class="guide-section panel">
          <div class="guide-heading"><span>02</span><div><div class="eyebrow">Backend settings</div><h2>后端配置</h2></div></div>
          <p>Git 中只保存 <code>.env.example</code> 和 <code>deploy/frp/frpc.toml.example</code> 两份示例。实际配置位于项目 <code>config/</code>，可以直接编辑；修改后重新运行安装脚本同步到 systemd 使用的受保护副本。</p>
          <div class="config-table">
            <div class="config-row config-head"><span>配置</span><span>用途</span><span>建议</span></div>
            <div class="config-row"><code>MC_API_TOKEN</code><span>解锁管理台的 Bearer Token</span><span>使用脚本生成的长随机值</span></div>
            <div class="config-row"><code>MC_STORAGE_ROOT</code><span>Map、Game、Backup 和制品根目录</span><span>保持在 WSL ext4，不要放 /mnt/c</span></div>
            <div class="config-row"><code>MC_PORT_MIN / MAX</code><span>游戏端口池</span><span>必须与 frpc 端口范围一致</span></div>
            <div class="config-row"><code>MC_BACKUP_LIMIT</code><span>每个 Game 的备份上限</span><span>默认 10，根据磁盘调整</span></div>
            <div class="config-row"><code>MC_RUNTIME_BACKEND</code><span>Paper 运行后端</span><span>生产保持 podman</span></div>
            <div class="config-row"><code>MC_JAVA_IMAGES_JSON</code><span>Java 主版本到容器镜像的映射</span><span>生产建议固定镜像 digest</span></div>
            <div class="config-row"><code>MC_MAX_UPLOAD_BYTES</code><span>地图和附加资源压缩包总大小</span><span>默认 2 GiB</span></div>
            <div class="config-row"><code>MC_RESOURCE_PACK_BASE_URL</code><span>玩家可访问的资源包 HTTP(S) 根地址</span><span>例如 https://packs.example.com</span></div>
          </div>
          <div class="command multiline"><code>sudo bash scripts/install-wsl.sh
sudo systemctl restart mc-manager-api mc-manager-worker</code><button @click="copy('sudo bash scripts/install-wsl.sh\nsudo systemctl restart mc-manager-api mc-manager-worker')">复制</button></div>
          <div class="guide-callout"><strong>如何查看 Token</strong><p>在 WSL 终端中运行下面命令。不要把输出发到聊天、日志或截图中。</p><div class="command"><code>grep '^MC_API_TOKEN=' config/mc-manager.env</code><button @click="copy(&quot;grep '^MC_API_TOKEN=' config/mc-manager.env&quot;)">复制</button></div></div>
        </section>

        <section id="frp" class="guide-section panel">
          <div class="guide-heading"><span>03</span><div><div class="eyebrow">Public access</div><h2>配置全局 frpc</h2></div></div>
          <p>如果只在本机测试，可以暂时不启动 frpc。需要公网连接 Minecraft 时再完成这一步。</p>
          <ol class="compact-steps">
            <li><b>准备公网 frps</b><span>确认服务器地址、服务端口、Token，以及允许映射的远端端口范围。</span></li>
            <li><b>编辑客户端配置</b><span>修改项目 <code>config/frpc.toml</code> 中的 <code>serverAddr</code>、<code>serverPort</code> 和端口范围。</span></li>
            <li><b>填写 Token</b><span>将与 frps 一致的 Token 写入项目 <code>config/frpc.toml</code> 的 <code>auth.token</code>；实际配置被 Git 忽略。</span></li>
            <li><b>校验并启动</b><span>配置校验通过后再启用 systemd 服务。</span></li>
            <li><b>暴露资源包下载</b><span>地图带玩家资源包时，另将本机 8080 映射到公网 HTTP 端口，最好由公网已有的 HTTPS 反向代理只转发 <code>/resource-packs/</code>。</span></li>
          </ol>
          <div class="command multiline"><code>sudo bash scripts/install-wsl.sh
sudo -u frp frpc verify -c /opt/mc-manager/config/frpc.toml
sudo systemctl enable --now frpc
systemctl status frpc</code><button @click="copy('sudo bash scripts/install-wsl.sh\nsudo -u frp frpc verify -c /opt/mc-manager/config/frpc.toml\nsudo systemctl enable --now frpc\nsystemctl status frpc')">复制</button></div>
          <div class="guide-callout warning"><strong>端口必须一致</strong><p>后端端口池、frpc 本地端口和 frps 远端允许端口必须对应。玩家连接地址是 <code>frps公网地址:分配端口</code>。</p></div>
          <div class="guide-callout"><strong>Paper 不直接托管本地 ZIP</strong><p>系统会把公开下载 URL、SHA-1 和接受策略写入 <code>server.properties</code>，Paper 负责在进服时通知客户端。资源包文件由本系统的匿名 <code>/resource-packs/</code> 路由下载，因此 <code>MC_RESOURCE_PACK_BASE_URL</code> 不能填写 <code>127.0.0.1</code>，也不能要求管理 Token。</p></div>
        </section>

        <section id="usage" class="guide-section panel">
          <div class="guide-heading"><span>04</span><div><div class="eyebrow">Daily workflow</div><h2>日常使用流程</h2></div></div>
          <div class="workflow-grid">
            <article><span>1</span><h3>上传 Map</h3><p>上传 <code>map.zip</code> 并填写运行版本；若有客户端材质，单独选择一个“玩家资源包”，可设置必须接受和提示语。</p></article>
            <article><span>2</span><h3>创建 Game</h3><p>在 Map 卡片上点击“创建游戏”。这一步只复制持久数据，不启动 Paper、不占端口。</p></article>
            <article><span>3</span><h3>启动 Game</h3><p>进入“游戏”，点击启动。推荐自动分配端口；等待任务显示 Paper 已就绪。</p></article>
            <article><span>4</span><h3>玩家连接</h3><p>本机测试连接 WSL 地址与端口；启用 FRP 后连接公网 frps 地址和相同端口。</p></article>
            <article><span>5</span><h3>停止并备份</h3><p>不要直接杀进程。点击“停止”，系统会优雅关闭 Paper、创建备份并释放端口。</p></article>
            <article><span>6</span><h3>恢复备份</h3><p>在 Game 详情的备份页选择恢复。系统会先保护当前状态；恢复后保持停止，需要手动启动。</p></article>
          </div>
          <div class="guide-callout"><strong>记住资源边界</strong><p><code>map_id</code> 只表示不可变仓库地图；<code>game_id</code> 表示可反复游玩的持久游戏；Backup 永远属于一个 Game。</p></div>
        </section>

        <section id="maps" class="guide-section panel">
          <div class="guide-heading"><span>05</span><div><div class="eyebrow">Import checklist</div><h2>地图上传要求</h2></div></div>
          <MapRequirements />
        </section>

        <section id="troubleshooting" class="guide-section panel">
          <div class="guide-heading"><span>06</span><div><div class="eyebrow">Troubleshooting</div><h2>常见问题排查</h2></div></div>
          <div class="faq-list">
            <details open><summary>管理台打不开</summary><p>先检查 API：<code>curl http://127.0.0.1:8080/healthz</code>。再查看 <code>systemctl status mc-manager-api</code> 和日志。</p></details>
            <details><summary>Token 一直提示无效</summary><p>确认复制时没有带上 <code>MC_API_TOKEN=</code> 前缀、空格或换行。Token 区分大小写。</p></details>
            <details><summary>Game 一直停留在任务中</summary><p>查看“任务”页错误，再运行 <code>journalctl -u mc-manager-worker -n 100 --no-pager</code>。不要直接修改数据库。</p></details>
            <details><summary>Paper 启动失败</summary><p>重点核对 Minecraft 版本、精确 Paper build、Java 主版本和插件兼容性。旧版本可能需要管理员提供受信任的 Paper URL 与 SHA-256。</p></details>
            <details><summary>玩家无法从公网连接</summary><p>确认 Game 已“运行中”，frpc 为 active，frps 防火墙和云安全组已开放分配端口，并检查 frps 的 allowPorts。</p></details>
            <details><summary>玩家资源包下载失败</summary><p>在 Map 详情点击“测试下载”，并从玩家所在网络访问同一 URL。确认公网 HTTPS/FRP 转发正常、<code>MC_RESOURCE_PACK_BASE_URL</code> 没有写成本机地址，修改地址后需重新导入 Map。</p></details>
            <details><summary>磁盘空间不足</summary><p>检查 <code>/srv/mc-manager</code> 和 Podman 存储。先停止新上传，不要手工删除正在使用的 Game 或 Backup 目录。</p></details>
          </div>
          <div class="command multiline"><code>systemctl status mc-manager-api mc-manager-worker frpc\njournalctl -u mc-manager-api -n 100 --no-pager\njournalctl -u mc-manager-worker -n 100 --no-pager</code><button @click="copy('systemctl status mc-manager-api mc-manager-worker frpc\njournalctl -u mc-manager-api -n 100 --no-pager\njournalctl -u mc-manager-worker -n 100 --no-pager')">复制</button></div>
          <div class="guide-callout danger"><strong>不要直接暴露到公网 HTTP</strong><p>当前 Token 会随 HTTP 请求发送。没有 HTTPS 时只在本机或可信网络使用；远程管理优先使用 SSH 隧道。</p></div>
        </section>
      </div>
    </div>
  </div>
</template>
