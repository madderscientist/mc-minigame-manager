<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { api, ApiError } from '../api/client'
import CopyAddress from '../components/CopyAddress.vue'
import CreateGameDialog from '../components/CreateGameDialog.vue'
import EmptyState from '../components/EmptyState.vue'
import QueryError from '../components/QueryError.vue'
import StatusBadge from '../components/StatusBadge.vue'
import StopGameDialog from '../components/StopGameDialog.vue'
import type { Game } from '../api/types'
import { useTaskStore } from '../stores/tasks'
import { useToastStore } from '../stores/toasts'
import { formatDate, runtimeLabels, toneForStatus } from '../utils/format'

const queryClient = useQueryClient()
const router = useRouter()
const tasks = useTaskStore()
const toasts = useToastStore()
const gamesQuery = useQuery({ queryKey: ['games'], queryFn: api.games, refetchInterval: 5000 })
const mapsQuery = useQuery({ queryKey: ['maps'], queryFn: api.maps })
const filter = ref<'all' | 'running' | 'stopped' | 'busy' | 'failed'>('all')
const createOpen = ref(false)
const startTarget = ref<Game | null>(null)
const stopTarget = ref<Game | null>(null)
const actionBusy = ref(false)
const manualPort = ref('')
const queryError = computed(() => gamesQuery.error.value ?? mapsQuery.error.value)

const games = computed(() => gamesQuery.data.value ?? [])
const filtered = computed(() => games.value.filter((game) => {
  if (filter.value === 'all') return true
  if (filter.value === 'running') return game.runtime_state === 'ready'
  if (filter.value === 'stopped') return !game.runtime_state || game.runtime_state === 'stopped' || game.runtime_state === 'failed'
  if (filter.value === 'busy') return ['preparing', 'starting', 'stopping', 'backing_up'].includes(game.runtime_state ?? game.state)
  return game.state === 'failed' || game.runtime_state === 'failed' || game.runtime_state === 'unknown'
}))

function statusText(game: Game) {
  if (game.runtime_state) return runtimeLabels[game.runtime_state]
  return game.state === 'ready' ? '已停止' : game.state === 'preparing' ? '创建中' : '创建失败'
}

function canStop(game: Game) {
  return game.runtime_state === 'ready' || game.runtime_state === 'unknown'
}

async function start() {
  if (!startTarget.value || actionBusy.value) return
  actionBusy.value = true
  try {
    const port = manualPort.value ? Number(manualPort.value) : undefined
    const accepted = await api.start(startTarget.value.game_id, port)
    tasks.track(accepted)
    toasts.push('启动任务已提交', `Game #${accepted.game_id}`, 'success')
    startTarget.value = null
    manualPort.value = ''
    await queryClient.invalidateQueries({ queryKey: ['games'] })
  } catch (reason) {
    toasts.push('启动失败', reason instanceof ApiError ? reason.message : '', 'danger')
  } finally { actionBusy.value = false }
}

async function stop(backup: boolean) {
  if (!stopTarget.value || actionBusy.value) return
  actionBusy.value = true
  try {
    const accepted = await api.stop(stopTarget.value.game_id, backup)
    tasks.track(accepted)
    toasts.push('停止任务已提交', backup ? 'Paper 停止后将创建备份' : '本次停止不会创建备份', 'success')
    stopTarget.value = null
    await queryClient.invalidateQueries({ queryKey: ['games'] })
  } catch (reason) {
    toasts.push('停止失败', reason instanceof ApiError ? reason.message : '', 'danger')
  } finally { actionBusy.value = false }
}
async function retryQueries() {
  await Promise.all([gamesQuery.refetch(), mapsQuery.refetch()])
}
function closeStart() { if (!actionBusy.value) startTarget.value = null }
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div><div class="eyebrow">Persistent games</div><h1>游戏</h1><p>管理可反复启动和停止的持久游戏副本。</p></div>
      <button class="button primary" :disabled="Boolean(queryError)||!mapsQuery.data.value?.length" @click="createOpen = true">＋ 创建游戏</button>
    </header>
    <div class="toolbar">
      <div class="segmented">
        <button v-for="item in [{k:'all',l:'全部'},{k:'running',l:'运行中'},{k:'stopped',l:'已停止'},{k:'busy',l:'操作中'},{k:'failed',l:'异常'}]" :key="item.k" :class="{active:filter===item.k}" @click="filter=item.k as typeof filter">{{ item.l }}</button>
      </div>
      <span class="result-count">{{ filtered.length }} 个游戏</span>
    </div>
    <QueryError v-if="queryError" :error="queryError" @retry="retryQueries" />
    <section v-else class="panel table-panel">
      <div v-if="filtered.length" class="data-table game-table">
        <div class="table-row table-head"><span>游戏</span><span>状态</span><span>连接地址</span><span>最后游玩</span><span>备份</span><span>操作</span></div>
        <div v-for="game in filtered" :key="game.game_id" class="table-row">
          <RouterLink :to="`/games/${game.game_id}`" class="entity-name"><div class="game-avatar">{{ game.name.slice(0,2).toUpperCase() }}</div><div><strong>{{ game.name }}</strong><small>Game #{{ game.game_id }} · Map #{{ game.map_id }} · MC {{ game.mc_version }}</small></div></RouterLink>
          <StatusBadge :label="statusText(game)" :tone="toneForStatus(game.runtime_state ?? game.state)" />
          <CopyAddress v-if="game.public_address" :address="game.public_address" /><span v-else class="muted-value">未运行</span>
          <span>{{ formatDate(game.last_played_at) }}</span><span>{{ game.backups.length }} 个</span>
          <div class="row-actions">
            <button v-if="game.state === 'ready' && (!game.runtime_state || ['stopped','failed'].includes(game.runtime_state))" class="button small primary" @click="startTarget=game">启动</button>
            <button v-else-if="canStop(game)" class="button small danger-outline" @click="stopTarget=game">{{ game.runtime_state === 'unknown' ? '尝试停止' : '停止' }}</button>
            <RouterLink v-else class="button small ghost" :to="`/games/${game.game_id}`">查看</RouterLink>
          </div>
        </div>
      </div>
      <EmptyState v-else title="还没有游戏" description="先上传地图，再从地图创建一个持久游戏。" icon="◆"><button v-if="mapsQuery.data.value?.length" class="button primary small" @click="createOpen=true">创建第一个游戏</button><RouterLink v-else class="button primary small" to="/maps">上传地图</RouterLink></EmptyState>
    </section>
    <CreateGameDialog :open="createOpen" :maps="mapsQuery.data.value ?? []" @close="createOpen=false" @created="(id)=>{createOpen=false;router.push(`/games/${id}`)}" />
    <StopGameDialog :open="Boolean(stopTarget)" :game-name="stopTarget?.name" :unknown-state="stopTarget?.runtime_state === 'unknown'" :busy="actionBusy" @close="stopTarget=null" @confirm="stop" />
    <Teleport to="body"><div v-if="startTarget" class="dialog-backdrop" @click.self="closeStart"><section class="dialog-card"><div class="dialog-icon">▶</div><h2>启动 {{ startTarget.name }}</h2><p>默认从全局端口池自动分配。Paper 完成启动后才可供玩家连接。</p><label class="field"><span>指定端口 <small>可选</small></span><input v-model="manualPort" type="number" min="1024" max="65535" placeholder="自动分配" /></label><div class="dialog-actions"><button class="button ghost" :disabled="actionBusy" @click="closeStart">取消</button><button class="button primary" :disabled="actionBusy" @click="start">{{ actionBusy?'提交中…':'启动游戏' }}</button></div></section></div></Teleport>
  </div>
</template>
