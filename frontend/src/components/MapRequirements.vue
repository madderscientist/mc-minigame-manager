<script setup lang="ts">
defineProps<{ compact?: boolean }>()
</script>

<template>
  <section class="requirements-card" :class="{ compact }">
    <div class="requirements-title">
      <span>✓</span>
      <div><strong>上传前请确认</strong><small>不符合要求的压缩包会被服务器拒绝</small></div>
    </div>
    <div class="requirements-grid">
      <div>
        <b>01 · 地图结构</b>
        <p>必须是 ZIP，且包含 <code>level.dat</code>。它可以位于压缩包根目录，或一个一级世界目录内。</p>
      </div>
      <div>
        <b>02 · 版本信息</b>
        <p>系统直接读取 <code>level.dat</code> 中的版本并自动选择 Paper build 和 Java；无法识别版本的地图会被拒绝。</p>
      </div>
      <div>
        <b>03 · 默认容量限制</b>
        <p>全部上传合计不超过 2 GiB；玩家资源包最多 250 MiB；地图解压后不超过 8 GiB、10 万文件。</p>
      </div>
      <div>
        <b>04 · 安全与文件名</b>
        <p>禁止绝对路径、<code>..</code>、符号链接和特殊文件。资源文件名仅使用字母、数字、点、横线和下划线。</p>
      </div>
    </div>
    <div class="requirements-warning">
      <strong>只上传可信内容</strong>
      <span>服务端目录中的插件 JAR 会在 Paper 启动时执行。显式选择的“玩家资源包”会由 Paper 提示玩家下载。</span>
    </div>
    <ul v-if="!compact" class="requirements-notes">
      <li>若上传的是裸世界，系统会放入 <code>world/</code>；若是完整服务端目录，世界目录应直接位于压缩包一级。</li>
      <li>系统会固定 <code>server-port=25565</code> 并设置正确的 <code>level-name</code>；外部端口由端口池分配。</li>
      <li>玩家资源包必须是 ZIP，且根目录直接包含有效的 <code>pack.mcmeta</code>。未单独上传时会使用地图根目录的 <code>resources.zip</code>；显式上传优先。</li>
      <li>系统会计算 Minecraft 使用的 SHA-1 并写入 <code>server.properties</code>。玩家下载 URL 必须已通过 <code>MC_RESOURCE_PACK_BASE_URL</code> 配置且公网可达。</li>
      <li>同版本默认复用仓库中最高的标准 Paper build；没有匹配时从 PaperMC 选择最新稳定 build。高级设置可覆盖。</li>
      <li>自定义 Paper URL 与 SHA-256 必须同时填写，且下载主机必须在后端允许列表中。</li>
    </ul>
  </section>
</template>
