<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { api, ApiError } from '../api/client'
import AddMapDialog from '../components/AddMapDialog.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import CreateGameDialog from '../components/CreateGameDialog.vue'
import EmptyState from '../components/EmptyState.vue'
import QueryError from '../components/QueryError.vue'
import StatusBadge from '../components/StatusBadge.vue'
import type { MapRecord } from '../api/types'
import { useToastStore } from '../stores/toasts'
import { toneForStatus } from '../utils/format'

const queryClient = useQueryClient()
const router = useRouter()
const toasts = useToastStore()
const mapsQuery = useQuery({ queryKey: ['maps'], queryFn: api.maps })
const gamesQuery = useQuery({ queryKey: ['games'], queryFn: api.games })
const uploadOpen = ref(false)
const createMap = ref<MapRecord | null>(null)
const deleteMap = ref<MapRecord | null>(null)
const busy = ref(false)
const queryError = computed(() => mapsQuery.error.value ?? gamesQuery.error.value)

function usageCount(mapId: number) { return gamesQuery.data.value?.filter((game) => game.map_id === mapId).length ?? 0 }
async function removeMap() {
  if (!deleteMap.value || busy.value) return
  busy.value = true
  try { await api.deleteMap(deleteMap.value.map_id); toasts.push('地图已删除', deleteMap.value.name, 'success'); deleteMap.value=null; await queryClient.invalidateQueries({queryKey:['maps']}) }
  catch(reason){ toasts.push('无法删除地图', reason instanceof ApiError?reason.message:'', 'danger') }
  finally{busy.value=false}
}
async function retryQueries() { await Promise.all([mapsQuery.refetch(), gamesQuery.refetch()]) }
</script>

<template>
  <div class="page">
    <header class="page-header"><div><div class="eyebrow">Immutable map library</div><h1>地图</h1><p>上传已有世界，或创建首次启动时自然生成的模板。</p></div><button class="button primary" @click="uploadOpen=true">＋ 添加地图</button></header>
    <QueryError v-if="queryError" :error="queryError" @retry="retryQueries" />
    <section class="map-grid" v-else-if="mapsQuery.data.value?.length">
      <article v-for="map in mapsQuery.data.value" :key="map.map_id" class="map-card">
        <RouterLink :to="`/maps/${map.map_id}`" class="map-cover"><div class="map-noise"/><span class="version-chip">MC {{ map.mc_version }}</span><div class="map-cube">▦</div></RouterLink>
        <div class="map-card-body"><div class="map-title"><div><h2>{{ map.name }}</h2><small>Map #{{ map.map_id }}</small></div><StatusBadge :label="map.state==='ready'?'可用':map.state==='preparing'?'导入中':'失败'" :tone="toneForStatus(map.state)" /></div>
          <dl class="map-meta"><div><dt>来源</dt><dd>{{ map.source_type === 'generated' ? '自然生成' : '上传' }}</dd></div><div><dt>Paper</dt><dd>{{ map.paper_build }}</dd></div><div><dt>Java</dt><dd>{{ map.java_major }}</dd></div><div><dt>游戏</dt><dd>{{ usageCount(map.map_id) }}</dd></div></dl>
          <div class="card-actions"><button class="button primary small" :disabled="map.state!=='ready'" @click="createMap=map">创建游戏</button><button class="button ghost small" :disabled="map.state!=='ready'||usageCount(map.map_id)>0" :title="usageCount(map.map_id)?'仍有游戏引用此模板':''" @click="deleteMap=map">删除模板</button></div>
        </div>
      </article>
    </section>
    <section v-else class="panel"><EmptyState title="仓库里还没有地图" description="上传已有世界或创建自然生成模板，随后可以创建持久游戏。" icon="▦"><button class="button primary" @click="uploadOpen=true">添加第一张地图</button></EmptyState></section>
    <AddMapDialog :open="uploadOpen" @close="uploadOpen=false" @added="async(id)=>{uploadOpen=false;await queryClient.invalidateQueries({queryKey:['maps']});router.push(`/maps/${id}`)}" />
    <CreateGameDialog :open="Boolean(createMap)" :maps="mapsQuery.data.value ?? []" :initial-map-id="createMap?.map_id" @close="createMap=null" @created="(id)=>{createMap=null;router.push(`/games/${id}`)}" />
    <ConfirmDialog :open="Boolean(deleteMap)" title="删除地图模板" :description="`将永久删除 ${deleteMap?.name ?? ''}。存在关联游戏时后端会拒绝。`" :confirm-text="deleteMap?.name" confirm-label="永久删除" danger :busy="busy" @close="deleteMap=null" @confirm="removeMap" />
  </div>
</template>
