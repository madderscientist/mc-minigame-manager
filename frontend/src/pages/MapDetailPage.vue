<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, ApiError } from '../api/client'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import CreateGameDialog from '../components/CreateGameDialog.vue'
import EmptyState from '../components/EmptyState.vue'
import QueryError from '../components/QueryError.vue'
import ServerSettingsSummary from '../components/ServerSettingsSummary.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useToastStore } from '../stores/toasts'
import { formatBytes, formatDate, toneForStatus } from '../utils/format'

const route = useRoute(); const router = useRouter(); const queryClient=useQueryClient(); const toasts=useToastStore(); const mapId=Number(route.params.mapId); const createOpen=ref(false); const deleteOpen=ref(false); const deleting=ref(false)
const mapQuery=useQuery({queryKey:['map',mapId],queryFn:()=>api.map(mapId)})
const gamesQuery=useQuery({queryKey:['games'],queryFn:api.games})
const related=computed(()=>gamesQuery.data.value?.filter((game)=>game.map_id===mapId)??[])
const mapMissing=computed(()=>mapQuery.error.value instanceof ApiError&&mapQuery.error.value.status===404)
const canCreate=computed(()=>mapQuery.data.value?.state==='ready')
const canDelete=computed(()=>gamesQuery.isSuccess.value&&related.value.length===0)

async function removeMap(){
  if(!mapQuery.data.value||!canDelete.value||deleting.value)return
  deleting.value=true
  try{
    await api.deleteMap(mapId)
    toasts.push('地图模板已删除',mapQuery.data.value.name,'success')
    await queryClient.invalidateQueries({queryKey:['maps']})
    await router.push('/maps')
  }catch(reason){
    toasts.push('无法删除地图模板',reason instanceof ApiError?reason.message:'','danger')
  }finally{
    deleting.value=false
    deleteOpen.value=false
  }
}
</script>

<template>
  <div class="page detail-page">
    <div class="breadcrumb"><RouterLink to="/maps">地图</RouterLink><span>/</span><span>Map #{{ mapId }}</span></div>
    <template v-if="mapQuery.data.value">
      <header class="detail-hero map-detail-hero"><div class="detail-symbol">▦</div><div class="detail-title"><div class="eyebrow">Immutable source map</div><h1>{{ mapQuery.data.value.name }}</h1><div class="detail-sub"><span>Map #{{ mapId }}</span><StatusBadge :label="mapQuery.data.value.state==='ready'?'可用':'不可用'" :tone="toneForStatus(mapQuery.data.value.state)" /></div></div><div class="header-actions"><button class="button primary" :disabled="!canCreate" :title="canCreate?'':'地图尚未准备完成'" @click="createOpen=true">＋ 从此地图创建游戏</button><button class="button ghost" :disabled="!canDelete" :title="canDelete?'删除此地图模板':'仍有游戏引用此模板'" @click="deleteOpen=true">删除模板</button></div></header>
      <section class="panel detail-facts"><div class="panel-heading"><div><span class="section-index">01</span><h2>运行环境</h2></div></div><dl class="fact-grid"><div><dt>来源</dt><dd>{{ mapQuery.data.value.source_type === 'generated' ? '自然生成模板' : '上传地图' }}</dd></div><div><dt>Minecraft</dt><dd>{{ mapQuery.data.value.mc_version }}</dd></div><div><dt>DataVersion</dt><dd>{{ mapQuery.data.value.data_version ?? (mapQuery.data.value.source_type === 'generated' ? '首次启动后生成' : '未识别') }}</dd></div><div><dt>Paper build</dt><dd>{{ mapQuery.data.value.paper_build }}</dd></div><div><dt>Java</dt><dd>Java {{ mapQuery.data.value.java_major }}</dd></div><div><dt>关联游戏</dt><dd>{{ related.length }}</dd></div></dl></section>
      <section class="panel"><div class="panel-heading"><div><span class="section-index">02</span><h2>默认服务端设置</h2></div><small>创建游戏时可以覆盖</small></div><ServerSettingsSummary :settings="mapQuery.data.value.server_settings" /></section>
      <section v-if="mapQuery.data.value.resource_pack" class="panel resource-pack-card">
        <div><div class="resource-pack-summary"><span>▣</span><div><h3>{{ mapQuery.data.value.resource_pack.filename }}</h3><p>{{ formatBytes(mapQuery.data.value.resource_pack.size_bytes) }} · pack_format {{ mapQuery.data.value.resource_pack.pack_format }} · <span :class="{'resource-pack-required':mapQuery.data.value.resource_pack.required}">{{ mapQuery.data.value.resource_pack.required ? '必须接受' : '允许拒绝' }}</span></p></div></div><p v-if="mapQuery.data.value.resource_pack.prompt">提示：{{ mapQuery.data.value.resource_pack.prompt }}</p><div class="resource-pack-hashes"><code>SHA-1 {{ mapQuery.data.value.resource_pack.sha1 }}</code><code>SHA-256 {{ mapQuery.data.value.resource_pack.sha256 }}</code></div></div>
        <a class="button ghost small" :href="mapQuery.data.value.resource_pack.url" target="_blank" rel="noopener">测试下载 ↗</a>
      </section>
      <section class="panel"><div class="panel-heading"><div><span class="section-index">{{ mapQuery.data.value.resource_pack ? '04' : '03' }}</span><h2>由此创建的游戏</h2></div><RouterLink to="/games">游戏列表 →</RouterLink></div>
        <div v-if="gamesQuery.isError.value"><p class="inline-error">关联游戏加载失败。</p><button class="button ghost small" @click="gamesQuery.refetch()">重新加载</button></div>
        <div v-else-if="related.length" class="compact-entities"><RouterLink v-for="game in related" :key="game.game_id" :to="`/games/${game.game_id}`"><div class="game-avatar">{{ game.name.slice(0,2).toUpperCase() }}</div><div><strong>{{ game.name }}</strong><small>Game #{{ game.game_id }} · {{ formatDate(game.last_played_at) }}</small></div><span>›</span></RouterLink></div>
        <EmptyState v-else title="还没有游戏" description="从此地图创建一个独立、可反复启停的游戏。" icon="◆"><button class="button primary small" :disabled="!canCreate" @click="createOpen=true">创建游戏</button></EmptyState>
      </section>
      <CreateGameDialog :open="createOpen" :maps="[mapQuery.data.value]" :initial-map-id="mapId" @close="createOpen=false" @created="(id)=>router.push(`/games/${id}`)" />
      <ConfirmDialog :open="deleteOpen" title="删除地图模板" :description="`将永久删除 ${mapQuery.data.value.name}。此操作不会保留模板文件。`" :confirm-text="mapQuery.data.value.name" confirm-label="永久删除" danger :busy="deleting" @close="deleteOpen=false" @confirm="removeMap" />
    </template>
    <EmptyState v-else-if="mapMissing" title="地图不存在" description="该地图可能已被删除。" icon="!" ><RouterLink class="button primary small" to="/maps">返回地图列表</RouterLink></EmptyState>
    <QueryError v-else-if="mapQuery.isError.value" :error="mapQuery.error.value" @retry="mapQuery.refetch()" />
  </div>
</template>
