<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { api, ApiError } from '../api/client'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import CreateGameDialog from '../components/CreateGameDialog.vue'
import EmptyState from '../components/EmptyState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import UploadMapDialog from '../components/UploadMapDialog.vue'
import type { MapRecord } from '../api/types'
import { useToastStore } from '../stores/toasts'
import { formatDate, toneForStatus } from '../utils/format'

const queryClient = useQueryClient()
const router = useRouter()
const toasts = useToastStore()
const mapsQuery = useQuery({ queryKey: ['maps'], queryFn: api.maps })
const gamesQuery = useQuery({ queryKey: ['games'], queryFn: api.games })
const uploadOpen = ref(false)
const createMap = ref<MapRecord | null>(null)
const deleteMap = ref<MapRecord | null>(null)
const busy = ref(false)

function usageCount(mapId: number) { return gamesQuery.data.value?.filter((game) => game.map_id === mapId).length ?? 0 }
async function removeMap() {
  if (!deleteMap.value) return
  busy.value = true
  try { await api.deleteMap(deleteMap.value.map_id); toasts.push('地图已删除', deleteMap.value.name, 'success'); deleteMap.value=null; await queryClient.invalidateQueries({queryKey:['maps']}) }
  catch(reason){ toasts.push('无法删除地图', reason instanceof ApiError?reason.message:'', 'danger') }
  finally{busy.value=false}
}
</script>

<template>
  <div class="page">
    <header class="page-header"><div><div class="eyebrow">Immutable map library</div><h1>地图</h1><p>不可变地图仓库。上传一次，可以创建多个独立游戏。</p></div><button class="button primary" @click="uploadOpen=true">⇧ 上传地图</button></header>
    <section class="map-grid" v-if="mapsQuery.data.value?.length">
      <article v-for="map in mapsQuery.data.value" :key="map.map_id" class="map-card">
        <RouterLink :to="`/maps/${map.map_id}`" class="map-cover"><div class="map-noise"/><span class="version-chip">MC {{ map.mc_version }}</span><div class="map-cube">▦</div></RouterLink>
        <div class="map-card-body"><div class="map-title"><div><h2>{{ map.name }}</h2><small>Map #{{ map.map_id }}</small></div><StatusBadge :label="map.state==='ready'?'可用':map.state==='preparing'?'导入中':'失败'" :tone="toneForStatus(map.state)" /></div>
          <dl class="map-meta"><div><dt>Paper</dt><dd>{{ map.paper_build }}</dd></div><div><dt>Java</dt><dd>{{ map.java_major }}</dd></div><div><dt>游戏</dt><dd>{{ usageCount(map.map_id) }}</dd></div><div><dt>创建</dt><dd>{{ formatDate(map.created_at) }}</dd></div></dl>
          <div class="card-actions"><button class="button primary small" @click="createMap=map">创建游戏</button><button class="button ghost small" :disabled="usageCount(map.map_id)>0" :title="usageCount(map.map_id)?'仍有游戏引用此地图':''" @click="deleteMap=map">删除</button></div>
        </div>
      </article>
    </section>
    <section v-else class="panel"><EmptyState title="仓库里还没有地图" description="上传第一张 Minecraft 地图，随后可以创建持久游戏。" icon="▦"><button class="button primary" @click="uploadOpen=true">上传第一张地图</button></EmptyState></section>
    <UploadMapDialog :open="uploadOpen" @close="uploadOpen=false" @uploaded="async(id)=>{uploadOpen=false;await queryClient.invalidateQueries({queryKey:['maps']});router.push(`/maps/${id}`)}" />
    <CreateGameDialog :open="Boolean(createMap)" :maps="mapsQuery.data.value ?? []" :initial-map-id="createMap?.map_id" @close="createMap=null" @created="(id)=>{createMap=null;router.push(`/games/${id}`)}" />
    <ConfirmDialog :open="Boolean(deleteMap)" title="删除仓库地图" :description="`将永久删除 ${deleteMap?.name ?? ''}。存在关联游戏时后端会拒绝。`" :confirm-text="deleteMap?.name" confirm-label="永久删除" danger :busy="busy" @close="deleteMap=null" @confirm="removeMap" />
  </div>
</template>
