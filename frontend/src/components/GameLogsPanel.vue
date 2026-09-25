<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { computed, ref, watch } from 'vue'

import { api } from '../api/client'
import EmptyState from './EmptyState.vue'
import QueryError from './QueryError.vue'

const props = defineProps<{ gameId: number }>()
const autoRefresh = ref(true)
const followLatest = ref(true)
const output = ref<HTMLElement | null>(null)
const logsQuery = useQuery({
  queryKey: computed(() => ['game-logs', props.gameId]),
  queryFn: () => api.gameLogs(props.gameId),
  refetchInterval: computed(() => autoRefresh.value ? 3000 : false),
  refetchOnWindowFocus: computed(() => autoRefresh.value),
  refetchOnReconnect: computed(() => autoRefresh.value),
  retry: false,
})
const logs = computed(() => logsQuery.data.value)
const text = computed(() => logs.value?.lines.join('\n') ?? '')
const updatedAt = computed(() => logsQuery.dataUpdatedAt.value
  ? new Date(logsQuery.dataUpdatedAt.value).toLocaleTimeString('zh-CN', { hour12: false })
  : '')

watch([text, followLatest, output], () => {
  if (followLatest.value && output.value) output.value.scrollTop = output.value.scrollHeight
}, { flush: 'post' })
</script>

<template>
  <section class="panel game-logs-panel">
    <div class="panel-heading logs-heading">
      <div><span class="section-index">LIVE LOG</span><h2>游戏日志</h2></div>
      <div class="logs-controls">
        <label><input v-model="autoRefresh" type="checkbox">自动刷新</label>
        <label><input v-model="followLatest" type="checkbox">跟随最新</label>
        <button class="button ghost small" :disabled="logsQuery.isFetching.value" @click="logsQuery.refetch()">
          {{ logsQuery.isFetching.value ? '刷新中…' : '刷新日志' }}
        </button>
      </div>
    </div>
    <p class="logs-description">最近一次运行的服务端日志，最多显示末尾 500 行 / 256 KiB。{{ autoRefresh ? '每 3 秒自动刷新。' : '自动刷新已暂停。' }}不包含容器启动前的输出。</p>
    <QueryError v-if="logsQuery.isError.value" title="日志加载失败" :error="logsQuery.error.value" @retry="logsQuery.refetch()" />
    <p v-if="logsQuery.isPending.value" class="logs-description" role="status">正在加载游戏日志…</p>
    <template v-if="logs">
      <div class="logs-meta"><span>{{ logsQuery.isError.value ? '上次成功读取的内容' : '最新日志' }} · {{ logs.lines.length }} 行<span v-if="logs.truncated"> · 较早内容已截断</span></span><span>更新于 {{ updatedAt }}</span></div>
      <pre v-if="logs.lines.length" ref="output" class="logs-output" aria-label="游戏日志内容" tabindex="0">{{ text }}</pre>
      <EmptyState v-else :title="logs.available ? '日志暂无内容' : '尚未生成日志'" description="游戏启动并生成服务端日志后会在这里显示；停止后仍可查看保留的日志。" icon="≡" />
    </template>
  </section>
</template>

<style scoped>
.game-logs-panel{min-width:0}
.logs-heading,.logs-controls,.logs-meta{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.logs-controls{justify-content:flex-start;font-size:.75rem}
.logs-controls label{display:flex;align-items:center;gap:6px;cursor:pointer}
.logs-controls input{accent-color:var(--green)}
.logs-description,.logs-meta{color:var(--muted);font-size:.75rem;line-height:1.7}
.logs-meta{margin:14px 0 8px}
.logs-output{margin:0;min-height:260px;max-height:65vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#142219;color:#dcebdc;border:1px solid #2c4633;border-radius:10px;padding:18px;font:12px/1.8 "DM Mono",monospace;tab-size:4;color-scheme:dark}
.logs-output:focus-visible{outline:2px solid var(--green);outline-offset:3px}
@media(max-width:600px){.logs-controls{width:100%}.logs-output{padding:12px;font-size:11px}}
</style>