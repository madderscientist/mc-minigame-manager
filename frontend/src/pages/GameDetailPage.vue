<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, ApiError } from '../api/client'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import EmptyState from '../components/EmptyState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TaskProgress from '../components/TaskProgress.vue'
import type { Backup } from '../api/types'
import { useTaskStore } from '../stores/tasks'
import { useToastStore } from '../stores/toasts'
import { formatBytes, formatDate, runtimeLabels, taskTypeLabels, toneForStatus } from '../utils/format'

const route=useRoute(); const router=useRouter(); const queryClient=useQueryClient(); const taskStore=useTaskStore(); const toasts=useToastStore(); const gameId=Number(route.params.gameId)
const tab=ref<'overview'|'backups'|'tasks'>('overview'); const startOpen=ref(false); const stopOpen=ref(false); const deleteOpen=ref(false); const restoreTarget=ref<Backup|null>(null); const deleteBackupTarget=ref<Backup|null>(null); const port=ref(''); const busy=ref(false)
const gameQuery=useQuery({queryKey:['game',gameId],queryFn:()=>api.game(gameId),refetchInterval:4000})
const mapId=computed(()=>gameQuery.data.value?.map_id)
const mapQuery=useQuery({queryKey:computed(()=>['map',mapId.value]),queryFn:()=>api.map(mapId.value!),enabled:computed(()=>mapId.value!==undefined)})
const backupQuery=useQuery({queryKey:['backups',gameId],queryFn:()=>api.backups(gameId),refetchInterval:6000})
const statusQuery=useQuery({queryKey:['status'],queryFn:api.status,refetchInterval:4000})
const gameTasks=computed(()=>statusQuery.data.value?.tasks.filter((task)=>task.game_id===gameId)??[])
const game=computed(()=>gameQuery.data.value); const running=computed(()=>game.value?.runtime_state==='ready'); const canStop=computed(()=>game.value?.runtime_state==='ready'||game.value?.runtime_state==='unknown'); const canStart=computed(()=>game.value?.state==='ready'&&(!game.value.runtime_state||['stopped','failed'].includes(game.value.runtime_state)))

async function start(){busy.value=true;try{const accepted=await api.start(gameId,port.value?Number(port.value):undefined);taskStore.track(accepted);toasts.push('启动任务已提交','','success');startOpen.value=false;port.value='';await refresh()}catch(r){toasts.push('启动失败',r instanceof ApiError?r.message:'','danger')}finally{busy.value=false}}
async function stop(){busy.value=true;try{const accepted=await api.stop(gameId);taskStore.track(accepted);toasts.push('停止任务已提交','完成后会自动备份','success');stopOpen.value=false;await refresh()}catch(r){toasts.push('停止失败',r instanceof ApiError?r.message:'','danger')}finally{busy.value=false}}
async function restore(){if(!restoreTarget.value)return;busy.value=true;try{const accepted=await api.load(gameId,restoreTarget.value.backup_id);taskStore.track(accepted);toasts.push('恢复任务已提交','当前状态会先创建保护备份','success');restoreTarget.value=null;tab.value='tasks';await refresh()}catch(r){toasts.push('恢复失败',r instanceof ApiError?r.message:'','danger')}finally{busy.value=false}}
async function removeBackup(){if(!deleteBackupTarget.value)return;busy.value=true;try{await api.deleteBackup(gameId,deleteBackupTarget.value.backup_id);toasts.push('备份已删除','','success');deleteBackupTarget.value=null;await backupQuery.refetch()}catch(r){toasts.push('删除失败',r instanceof ApiError?r.message:'','danger')}finally{busy.value=false}}
async function removeGame(){busy.value=true;try{const accepted=await api.deleteGame(gameId);taskStore.track(accepted);toasts.push('删除任务已提交','','success');deleteOpen.value=false;await router.push('/games')}catch(r){toasts.push('无法删除游戏',r instanceof ApiError?r.message:'','danger')}finally{busy.value=false}}
async function refresh(){await Promise.all([queryClient.invalidateQueries({queryKey:['game',gameId]}),queryClient.invalidateQueries({queryKey:['status']})])}
function stateLabel(){if(game.value?.runtime_state)return runtimeLabels[game.value.runtime_state];return game.value?.state==='ready'?'已停止':game.value?.state==='preparing'?'创建中':'创建失败'}
function reasonLabel(reason:string){return ({normal_stop:'正常停止',crash_snapshot:'异常快照',before_restore:'恢复前保护'} as Record<string,string>)[reason]??reason}
</script>

<template>
  <div class="page detail-page">
    <div class="breadcrumb"><RouterLink to="/games">游戏</RouterLink><span>/</span><span>Game #{{ gameId }}</span></div>
    <template v-if="game">
      <header class="detail-hero"><div class="game-avatar detail-avatar">{{ game.name.slice(0,2).toUpperCase() }}</div><div class="detail-title"><div class="eyebrow">Persistent game</div><h1>{{ game.name }}</h1><div class="detail-sub"><span>Game #{{ game.game_id }}</span><StatusBadge :label="stateLabel()" :tone="toneForStatus(game.runtime_state??game.state)" /><code v-if="game.port">:{{ game.port }}</code></div></div><div class="header-actions"><button v-if="canStart" class="button primary" @click="startOpen=true">▶ 启动</button><button v-if="canStop" class="button danger-outline" @click="stopOpen=true">■ {{ game.runtime_state==='unknown'?'尝试停止':'停止' }}</button><button class="button ghost" @click="deleteOpen=true">删除</button></div></header>
      <nav class="detail-tabs"><button :class="{active:tab==='overview'}" @click="tab='overview'">概况</button><button :class="{active:tab==='backups'}" @click="tab='backups'">备份 <span>{{ backupQuery.data.value?.length??0 }}</span></button><button :class="{active:tab==='tasks'}" @click="tab='tasks'">任务记录 <span>{{ gameTasks.length }}</span></button></nav>
      <template v-if="tab==='overview'">
        <section class="detail-overview-grid"><article class="panel status-focus"><span class="section-index">01</span><div class="status-orb" :class="toneForStatus(game.runtime_state??game.state)"><i/></div><h2>{{ stateLabel() }}</h2><p v-if="running">Paper 已就绪，玩家可通过端口 {{ game.port }} 连接。</p><p v-else-if="game.runtime_state==='unknown'">无法确认容器状态，请尝试停止并检查任务结果。</p><p v-else>游戏数据已安全保存在持久目录中。</p><button v-if="canStart" class="button primary wide" @click="startOpen=true">启动这个游戏</button><button v-if="canStop" class="button danger-outline wide" @click="stopOpen=true">{{ game.runtime_state==='unknown'?'尝试停止异常运行':'停止并创建备份' }}</button></article>
          <article class="panel"><div class="panel-heading"><div><span class="section-index">02</span><h2>游戏信息</h2></div></div><dl class="fact-list"><div><dt>来源地图</dt><dd><RouterLink :to="`/maps/${game.map_id}`">{{ mapQuery.data.value?.name??`Map #${game.map_id}` }} →</RouterLink></dd></div><div><dt>创建时间</dt><dd>{{ formatDate(game.created_at) }}</dd></div><div><dt>最后游玩</dt><dd>{{ formatDate(game.last_played_at) }}</dd></div><div><dt>运行端口</dt><dd>{{ game.port?`:${game.port}`:'未分配' }}</dd></div><div><dt>备份数量</dt><dd>{{ backupQuery.data.value?.length??0 }} 个</dd></div></dl></article>
          <article class="panel recent-task"><div class="panel-heading"><div><span class="section-index">03</span><h2>最近任务</h2></div></div><TaskProgress v-if="gameTasks[0]" :task="gameTasks[0]" compact/><EmptyState v-else title="暂无任务" description="启动或停止后会出现任务记录。" icon="↻"/></article></section>
      </template>
      <section v-else-if="tab==='backups'" class="panel backup-panel"><div class="panel-heading"><div><span class="section-index">02</span><h2>备份时间线</h2></div><small>自动保留有限数量</small></div>
        <div v-if="backupQuery.data.value?.length" class="backup-timeline"><article v-for="backup in backupQuery.data.value" :key="backup.backup_id" class="backup-row"><div class="timeline-dot" :class="backup.clean_shutdown?'clean':'dirty'"/><div class="backup-main"><div><strong>{{ formatDate(backup.created_at) }}</strong><StatusBadge :label="backup.clean_shutdown?'干净备份':'非正常快照'" :tone="backup.clean_shutdown?'success':'danger'"/></div><span>{{ reasonLabel(backup.reason) }} · {{ formatBytes(backup.size_bytes) }}</span><code>{{ backup.backup_id }}</code></div><div class="row-actions"><button class="button small ghost" @click="restoreTarget=backup">恢复</button><button class="icon-button danger-text" title="删除备份" @click="deleteBackupTarget=backup">×</button></div></article></div>
        <EmptyState v-else title="还没有备份" description="游戏正常停止后会自动创建第一个备份。" icon="◌"/>
      </section>
      <section v-else class="panel"><div class="panel-heading"><div><span class="section-index">03</span><h2>任务记录</h2></div></div><div v-if="gameTasks.length" class="task-list"><article v-for="task in gameTasks" :key="task.task_id" class="task-list-row"><div><strong>{{ taskTypeLabels[task.type] }}</strong><small>{{ formatDate(task.created_at) }}</small></div><TaskProgress :task="task" compact/></article></div><EmptyState v-else title="没有任务记录" description="该游戏尚未执行创建以外的操作。" icon="↻"/></section>
      <ConfirmDialog :open="stopOpen" title="停止游戏" :description="game.runtime_state==='unknown'?'运行状态未知。系统将尝试终止对应容器，并根据实际退出结果创建备份。':'Paper 将优雅停止，随后自动创建备份并释放端口。'" confirm-label="停止并备份" danger :busy="busy" @close="stopOpen=false" @confirm="stop"/>
      <ConfirmDialog :open="Boolean(restoreTarget)" title="恢复历史备份" :description="`当前状态会先创建保护备份，然后用 ${formatDate(restoreTarget?.created_at)} 的内容覆盖 Game #${gameId}。恢复完成后不会自动启动。`" :confirm-text="String(gameId)" confirm-label="保护并恢复" danger :busy="busy" @close="restoreTarget=null" @confirm="restore"/>
      <ConfirmDialog :open="Boolean(deleteBackupTarget)" title="删除备份" :description="`将永久删除 ${formatDate(deleteBackupTarget?.created_at)} 的恢复点。`" :confirm-text="deleteBackupTarget?.backup_id" confirm-label="永久删除" danger :busy="busy" @close="deleteBackupTarget=null" @confirm="removeBackup"/>
      <ConfirmDialog :open="deleteOpen" title="删除整个游戏" description="将删除游戏数据和全部内部备份。运行中的游戏不能删除。" :confirm-text="String(gameId)" confirm-label="永久删除游戏" danger :busy="busy" @close="deleteOpen=false" @confirm="removeGame"/>
      <Teleport to="body"><div v-if="startOpen" class="dialog-backdrop" @click.self="startOpen=false"><section class="dialog-card"><div class="dialog-icon">▶</div><h2>启动 {{ game.name }}</h2><p>默认自动分配端口；在 Paper 进入就绪前端口仅为预留状态。</p><label class="field"><span>指定端口 <small>可选</small></span><input v-model="port" type="number" min="1024" max="65535" placeholder="自动分配"/></label><div class="dialog-actions"><button class="button ghost" @click="startOpen=false">取消</button><button class="button primary" :disabled="busy" @click="start">{{ busy?'提交中…':'启动游戏' }}</button></div></section></div></Teleport>
    </template>
    <EmptyState v-else-if="gameQuery.isError.value" title="游戏不存在" description="该游戏可能已被删除。" icon="!"><RouterLink class="button primary small" to="/games">返回游戏列表</RouterLink></EmptyState>
  </div>
</template>
